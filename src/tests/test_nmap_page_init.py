import logging
import os
import sys

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GtkSource', '5')
from gi.repository import Adw, Gio, GLib, Gtk

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    from constants import APP_ID, RESOURCE_PREFIX
    logger.info(f"Successfully imported APP_ID: {APP_ID} and RESOURCE_PREFIX: {RESOURCE_PREFIX}")
except ImportError as e:
    logger.error(f"Failed to import from constants: {e}. Attempting to set fallback values.")
    APP_ID = "com.github.mclellac.woes"
    RESOURCE_PREFIX = "/com/github/mclellac/woes/gtk"

class MockSettings:
    def __init__(self, schema_id):
        logger.debug(f"MockSettings: Initializing with schema_id: {schema_id}")
        self.schema_id = schema_id
        self._settings = {
            "source-style-scheme": "default-style"
        }
        if schema_id != APP_ID:
            logger.warning(f"MockSettings: schema_id '{schema_id}' does not match expected APP_ID '{APP_ID}'")

    def get_string(self, key):
        val = self._settings.get(key, "")
        logger.debug(f"MockSettings: get_string('{key}') called, returning '{val}'.")
        return val

    def connect(self, signal, callback):
        logger.debug(f"MockSettings: connect for signal '{signal}' called with callback {callback}.")
        pass

    def get_boolean(self, key, default=False):
        val = self._settings.get(key, default)
        logger.debug(f"MockSettings: get_boolean('{key}') returning {val}")
        return val

    def get_int(self, key, default=0):
        val = self._settings.get(key, default)
        logger.debug(f"MockSettings: get_int('{key}') returning {val}")
        return val

Gio.Settings = MockSettings

try:
    from nmap_page import NmapPage
    logger.info("Successfully imported NmapPage.")
except Exception as e:
    logger.error(f"Failed to import NmapPage: {e}", exc_info=True)
    sys.exit(1)

class TestApplication(Adw.Application):
    def __init__(self, **kwargs):
        logger.info("TestApplication: Initializing.")
        super().__init__(application_id=APP_ID, **kwargs)
        self.window = None
        self.nmap_page_instance = None

    def do_activate(self):
        logger.info("TestApplication: do_activate called.")

        if not self.window:
            logger.info("TestApplication: Creating dummy window and attempting NmapPage instantiation.")
            try:
                logger.info("Attempting to instantiate NmapPage...")
                self.nmap_page_instance = NmapPage()
                logger.info("Successfully instantiated NmapPage.")

                if hasattr(self.nmap_page_instance, 'error_banner') and self.nmap_page_instance.error_banner is not None:
                    logger.info("NmapPage.error_banner found and is not None.")
                else:
                    logger.error("NmapPage.error_banner is None or not found! This is an issue.")

                if hasattr(self.nmap_page_instance, 'nmap_target_listbox') and self.nmap_page_instance.nmap_target_listbox is not None:
                    logger.info("NmapPage.nmap_target_listbox found and is not None.")
                else:
                    logger.error("NmapPage.nmap_target_listbox is None or not found! This is an issue.")

            except Exception as e:
                logger.error(f"Error during NmapPage instantiation or check: {e}", exc_info=True)
                self.quit()
                sys.exit(1)

            logger.info("Test completed. Quitting application.")
            self.quit()

if __name__ == "__main__":
    logger.info("Starting test_nmap_page_init.py script.")
    schema_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'data'))
    os.environ['GSETTINGS_SCHEMA_DIR'] = schema_dir
    logger.info(f"Set GSETTINGS_SCHEMA_DIR to: {schema_dir}")

    app = TestApplication()
    exit_status = app.run(sys.argv)
    logger.info(f"TestApplication finished with exit_status: {exit_status}")

    if app.nmap_page_instance:
        if not (hasattr(app.nmap_page_instance, 'error_banner') and app.nmap_page_instance.error_banner is not None):
            logger.error("Final check: NmapPage.error_banner is None or not found after app run.")
            sys.exit(1)
        if not (hasattr(app.nmap_page_instance, 'nmap_target_listbox') and app.nmap_page_instance.nmap_target_listbox is not None):
            logger.error("Final check: NmapPage.nmap_target_listbox is None or not found after app run.")
            sys.exit(1)
        logger.info("Final checks passed: error_banner and nmap_target_listbox are present.")
    else:
        logger.error("Final check: nmap_page_instance was not created.")
        sys.exit(1)

    sys.exit(0)

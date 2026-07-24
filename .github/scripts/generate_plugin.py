import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CORE_FILE = os.path.join(SCRIPT_DIR, '../../plugin_core.py')
OUTPUT_FILE = os.path.join(SCRIPT_DIR, '../../plugin.py')
PLUGIN_VERSION = "2.22.3"  # x-release-please-version
RELEASE_VERSION_MARKER = "x-release-please-" + "version"

def generate_plugin():
    xml_header = f'''# PyPluginStore - PyPluginStore
#
# Author: adrighem, 2018
#
#  Since (2018-02-23): Initial Version
#

"""
<plugin key="PP-MANAGER" name="PyPluginStore" author="adrighem" version="{PLUGIN_VERSION}" externallink="https://forum.domoticz.com/viewtopic.php?t=44626"> <!-- {RELEASE_VERSION_MARKER} -->
    <description>
        <h2>PyPluginStore</h2><br/>
        This plugin manages other Domoticz Python plugins.<br/><br/>
        <b>Usage:</b><br/>
        1. Add this hardware to Domoticz.<br/>
        2. Navigate to <b>Custom</b> -> <b>pypluginstore</b> in the top menu to manage your plugins.
    </description>
    <params>
        <param field="Mode4" label="Auto Update" width="175px">
            <options>
                <option label="All" value="All"/>
                <option label="All (NotifyOnly)" value="AllNotify" default="true"/>
                <option label="None" value="None"/>
            </options>
        </param>
        <param field="Mode6" label="Debug" width="75px">
            <options>
                <option label="True" value="Debug"/>
                <option label="False" value="Normal" default="true" />
            </options>
        </param>
        <param field="Mode7" label="Git Ownership Repair" width="175px">
            <options>
                <option label="Disabled" value="Disabled" default="true"/>
                <option label="Enabled" value="Enabled"/>
            </options>
        </param>
    </params>
</plugin>
"""
PYPLUGINSTORE_VERSION = "{PLUGIN_VERSION}"  # {RELEASE_VERSION_MARKER}
'''

    with open(CORE_FILE, 'r', encoding='utf-8') as f:
        core_code = f.read()

    with open(OUTPUT_FILE, 'w', encoding='utf-8', newline='\n') as f:
        f.write(xml_header + "\n" + core_code)

    print("plugin.py successfully generated.")

if __name__ == '__main__':
    generate_plugin()

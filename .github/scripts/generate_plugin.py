import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CORE_FILE = os.path.join(SCRIPT_DIR, '../../plugin_core.py')
OUTPUT_FILE = os.path.join(SCRIPT_DIR, '../../plugin.py')

def generate_plugin():
    xml_header = f'''# PyPluginStore - PyPluginStore
#
# Author: adrighem, 2018
#
#  Since (2018-02-23): Initial Version
#


"""
<plugin key="PP-MANAGER" name="PyPluginStore" author="adrighem" version="2.6.0" externallink="https://www.domoticz.com/forum/viewtopic.php?f=65&t=22339"> <!-- x-release-please-version -->
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
    </params>
</plugin>
"""
'''

    with open(CORE_FILE, 'r') as f:
        core_code = f.read()

    with open(OUTPUT_FILE, 'w') as f:
        f.write(xml_header + "\n" + core_code)

    print("plugin.py successfully generated.")

if __name__ == '__main__':
    generate_plugin()

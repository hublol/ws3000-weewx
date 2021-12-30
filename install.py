# installer for the WS-3000 driver
# Distributed under the terms of the GNU Public License (GPLv3)

from weecfg.extension import ExtensionInstaller

def loader():
    return WS3000Installer()

class WS3000Installer(ExtensionInstaller):
    def __init__(self):
        super(WS3000Installer, self).__init__(
            version="0.3",
            name='WS-3000',
            description='Weewx driver for the WS-3000 station',
            author="hublol",
            author_email="hal.lol@tutanota.com",
            config={
                'WS3000': {
                    'driver': 'user.ws3000',
                    'model': 'WS3000',
                    'timeout': '1000'
                    }
                },
            files=[('bin/user', ['bin/user/ws3000.py', 'bin/user/ws3000Extensions.py'])]
            )
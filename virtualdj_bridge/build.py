"""Build the universal macOS VirtualDJ manual sound-effect plugin; optionally install it."""
import argparse
from datetime import datetime
from pathlib import Path
import plistlib
import shutil
import subprocess

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--install', action='store_true')
args = parser.parse_args()
xcode = Path('/Applications/Xcode.app/Contents/Developer')
compiler = xcode / 'Toolchains/XcodeDefault.xctoolchain/usr/bin/clang++'
sdk = xcode / 'Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk'
bundle = root / 'build/LEDStemBridge.bundle'
executable = bundle / 'Contents/MacOS/LEDStemBridge'
executable.parent.mkdir(parents=True, exist_ok=True)
subprocess.run([str(compiler), '-std=c++11', '-O2', '-bundle', '-arch', 'arm64',
                '-arch', 'x86_64', '-isysroot', str(sdk), '-framework', 'CoreFoundation',
                str(root / 'StemBridge.cpp'), '-o', str(executable)], check=True)
with (bundle / 'Contents/Info.plist').open('wb') as out:
    plistlib.dump(dict(CFBundleExecutable='LEDStemBridge', CFBundleIdentifier='local.music.ledstembridge',
                      CFBundleName='LED Stem Bridge', CFBundlePackageType='BNDL',
                      CFBundleVersion='2', CFBundleShortVersionString='1.1'), out)
subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(bundle)], check=True)
print(f'Built {bundle}')
if args.install:
    home = Path.home() / 'Library/Application Support/VirtualDJ'
    if not home.is_dir():
        raise SystemExit('VirtualDJ user folder not found; copy bundle to your SoundEffect plugin folder.')
    for directory in ('PluginsMacArm', 'Plugins64'):
        destination = home / directory / 'SoundEffect' / bundle.name
        shutil.copytree(bundle, destination, dirs_exist_ok=True)
        print(f'Installed {destination}')
        old = home / directory / 'AutoStart' / bundle.name
        if old.exists():
            backup = home / 'LEDStemBridge-backups' / datetime.now().strftime('%Y%m%d-%H%M%S-%f') / directory / old.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(backup))
            print(f'Backed up old AutoStart copy: {backup}')
    print('Restart VirtualDJ, then select and enable LED Stem Bridge in an effect slot (one instance only). No audio routing changes are made.')

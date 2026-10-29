# Third-party notices

The controller's own Swift and Python source is MIT licensed. Hermes, DeepSeek Harness, LM Studio, TensorFold, llama.cpp, model weights and their logos are separate products, with separate licenses and terms. They are not bundled or relicensed here. No affiliation or endorsement is implied.

Binary builds contain CPython and its standard library (Python Software Foundation license and notices), psutil (BSD-3-Clause), PyYAML (MIT), and websocket-client (Apache-2.0). PyInstaller's bootloader is GPL-2.0-or-later with its distribution exception. `build.sh` includes their original notices under `Contents/Resources/ThirdPartyLicenses`. The build also includes the upstream Python distribution's component notices from `third_party/python-licenses`, covering statically linked libraries such as OpenSSL. Optional component notices are preserved even when a particular platform does not use them.

The app icon is an original geometric drawing. Apple frameworks and system symbols used at runtime remain subject to Apple's terms; this license does not transfer rights to Apple's assets.

Optional llama.cpp instrumentation targets upstream `ggml-org/llama.cpp` commit `36e36e63a54fc8f85b95e9c499a022b597e478dd` (b11840). A resulting engine remains a separate llama.cpp build with its upstream license and notices; no modified engine binary is included in this controller's source or app bundle.

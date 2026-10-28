# Installation and release limitations

The first preview provides an Apple silicon DMG containing a native SwiftUI app and its frozen Python bridge. It does not include engines, agents or model weights. Download from this repository's Releases page, compare the SHA-256 file if desired, open the DMG, and drag the app to Applications.

The preview is ad-hoc signed, not signed with an Apple Developer ID and not notarized. macOS may require the owner to allow it through the system's normal opening flow. The project does not provide commands to disable Gatekeeper, remove quarantine or alter system security settings. If your Mac or organization does not permit opening it, use the source build or wait for a notarized release.

macOS 13 or newer is the source deployment target. The initial binary is validated on the development Mac, not every supported macOS release or engine version. Intel builds must be made on Intel (or with a verified universal toolchain and matching dependencies); do not rename an arm64 package as universal.

## Updating and rollback

Quit the controller before replacing its app bundle. Closing it does not stop model services. Replace only the app, never the data folder. Numeric history and local configuration live in `~/Library/Application Support/Local Model Controller` and survive upgrades. Keep the previous app if you want a simple rollback.

## Uninstalling

Move the app to Trash. This does not remove model engines, weights or your controller history. If you also want to remove controller data, move that data folder to Trash yourself after reviewing configuration backups. No system launch agent or recurring job is installed by the controller.

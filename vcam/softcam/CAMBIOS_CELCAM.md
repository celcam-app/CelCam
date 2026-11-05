# Changes made for CelCam

This folder is [Softcam](https://github.com/tshino/softcam) by tshino (MIT license, see `LICENSE`), at commit
`6f5d8f9b5f7dc37a86a119b881bc75e1860fb3ec`, with these changes:

- **Own identity:** filter name `CelCam`, new CLSID, and own names for the shared memory and the mutex, so it does not
  collide with other Softcam-based programs.
- **Always available:** an application can open the camera before CelCam is running. It uses the size remembered in
  `HKCU\Software\CelCam` (or 1280x720) and shows black until CelCam starts.
- **Shared memory released while idle:** an application that only lists cameras no longer prevents CelCam from
  creating the camera again.
- **Frame rate only announced, never enforced:** `scSendFrame` no longer sleeps, because frames arrive from a phone in
  real time. Without a sender, the camera announces the rate remembered in the registry (or 30). A running application
  only attaches to a stream with the same size and rate.
- **30 fps declared** when the sender sets no frame rate (it was 60).
- **Static C runtime** in Release builds, so it needs no Visual C++ redistributable.
- **Version information** in `src/softcam/celcam_vcam.rc`.

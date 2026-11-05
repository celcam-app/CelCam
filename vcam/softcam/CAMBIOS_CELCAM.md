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
  real time. Without a sender, the camera announces the rate remembered in the registry (or 30).
- **Format changes without interruption:** `scSetFormat` changes size and rate while running, and the shared memory has
  room for 1920x1080. An application keeps the size it negotiated: the filter scales the image to it (bilinear, centered,
  black bars if the shape differs).
- **Several formats offered:** the current one first, then 1080p, 720p, 540p, 480p, 640x480, 360p and 240p at 30, 60 and
  120 frames per second. The rate is informative: frames are delivered as they arrive.
- **Real timestamps:** each sample carries the graph's stream time instead of a fixed counter, and quality messages no
  longer change the timing.
- The upstream tests in `tests` were not adapted to these changes and are not part of the build.
- **30 fps declared** when the sender sets no frame rate (it was 60).
- **Static C runtime** in Release builds, so it needs no Visual C++ redistributable.
- **Version information** in `src/softcam/celcam_vcam.rc`.

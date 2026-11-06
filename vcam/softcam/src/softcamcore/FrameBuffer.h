#pragma once

#include <cstdint>
#include <cstddef>
#include <vector>
#include "Misc.h"
#include "Watchdog.h"


namespace softcam {


using std::uint64_t;
using std::uint32_t;
using std::uint16_t;


/// Shared frame buffer between processes (sender and receiver)
class FrameBuffer
{
 public:
    // CelCam: hay varias cámaras, cada una con su memoria (`index`). Los cuadros son BGR o BGRA (`channels`).
    static constexpr int MaxCameras = 4;

    static FrameBuffer create(
                        int             index,
                        int             width,
                        int             height,
                        float           framerate = 0.0f,
                        int             channels = 3);
    static FrameBuffer open(int index);

    FrameBuffer& operator =(const FrameBuffer&);
    explicit operator bool() const { return handle() != nullptr; }

    void*           handle() const;
    int             width() const;
    int             height() const;
    float           framerate() const;
    int             channels() const;
    uint64_t        frameCounter() const;
    bool            active() const;
    bool            connected() const;

    void            deactivate();
    void            write(const void* image_bits);
    // CelCam: cambia el formato sin recrear la memoria. Si no hay imagen, queda en negro.
    bool            setFormat(int width, int height, float framerate, int channels, const void* image_bits);
    // CelCam: adapta la imagen al tamaño y al formato (3 o 4 canales) que negoció la app.
    void            transferToDIB(
                        void*                   image_bits,
                        int                     dest_width,
                        int                     dest_height,
                        int                     dest_channels,
                        std::vector<uint8_t>&   scratch,
                        uint64_t*               out_frame_counter);
    bool            waitForNewFrame(uint64_t frame_counter, float time_out = 0.5f);

    void            release();

    static constexpr float WATCHDOG_HEARTBEAT_INTERVAL = 0.02f;
    static constexpr float WATCHDOG_MONITOR_INTERVAL = 0.02f;
    static constexpr float WATCHDOG_TIMEOUT = 0.5f;

 private:
    struct Header;

    mutable NamedMutex      m_mutex;
    SharedMemory            m_shmem;
    Watchdog                m_sender_watchdog;
    Watchdog                m_receiver_watchdog;

    explicit FrameBuffer(const char* mutex_name) : m_mutex(mutex_name) {}

    Header*         header();
    const Header*   header() const;

    static bool     checkDimensions(
                        int width,
                        int height);
    static uint32_t calcMemorySize(
                        uint16_t width,
                        uint16_t height,
                        int      channels);
};


} //namespace softcam

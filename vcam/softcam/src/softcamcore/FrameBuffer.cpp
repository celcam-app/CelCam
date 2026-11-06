#include "FrameBuffer.h"

#include <windows.h>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <mutex> // lock_guard
#include <string>


namespace {

// CelCam: lo que se ve detrás de una imagen transparente en una app que no admite transparencia.
const std::uint8_t FallbackBGR[3] = { 24, 19, 17 };

inline void convertPixel(const std::uint8_t* src, int channels, std::uint8_t* dest, int dest_channels)
{
    if (channels == 3)
    {
        dest[0] = src[0];
        dest[1] = src[1];
        dest[2] = src[2];
        if (dest_channels == 4)
        {
            dest[3] = 255;
        }
    }
    else if (dest_channels == 4)
    {
        std::memcpy(dest, src, 4);
    }
    else
    {
        const int alpha = src[3];
        for (int c = 0; c < 3; c++)
        {
            dest[c] = (std::uint8_t)((src[c] * alpha + FallbackBGR[c] * (255 - alpha) + 127) / 255);
        }
    }
}

void copyToDIB(const std::uint8_t* image, int w, int h, int channels, void* image_bits, int dest_channels)
{
    const int stride = (w * dest_channels + 3) & ~3;
    std::uint8_t* row = (std::uint8_t*)image_bits;
    for (int y = 0; y < h; y++)
    {
        const std::uint8_t* src = image + (std::size_t)channels * w * (h - 1 - y);
        if (channels == dest_channels)
        {
            std::memcpy(row, src, (std::size_t)channels * w);
        }
        else
        {
            for (int x = 0; x < w; x++)
            {
                convertPixel(src + channels * x, channels, row + dest_channels * x, dest_channels);
            }
        }
        row += stride;
    }
}

struct ScaleStep
{
    int     m_index;
    int     m_weight; // de 0 a 256, hacia el píxel siguiente
};

std::vector<ScaleStep> makeScaleSteps(int source, int target)
{
    std::vector<ScaleStep> steps(target);
    for (int i = 0; i < target; i++)
    {
        const double position = (std::max)(0.0, (i + 0.5) * source / target - 0.5);
        const int index = (std::min)((int)position, source - 2);
        steps[i] = ScaleStep{ index, (int)((position - index) * 256) };
    }
    return steps;
}

// Escala bilineal, con la imagen entera y centrada. Lo que sobra queda como está:
// quien llama ya dejó el DIB en negro.
void scaleToDIB(const std::uint8_t* image, int w, int h, int channels, void* image_bits, int dest_w, int dest_h, int dest_channels)
{
    const double scale = (std::min)((double)dest_w / w, (double)dest_h / h);
    const int target_w = (std::min)(dest_w, (int)std::lround(w * scale));
    const int target_h = (std::min)(dest_h, (int)std::lround(h * scale));
    const int offset_x = (dest_w - target_w) / 2;
    const int offset_y = (dest_h - target_h) / 2;
    const int stride = (dest_w * dest_channels + 3) & ~3;
    const auto columns = makeScaleSteps(w, target_w);
    const auto rows = makeScaleSteps(h, target_h);
    for (int y = 0; y < target_h; y++)
    {
        const std::uint8_t* upper = image + (std::size_t)channels * w * rows[y].m_index;
        const std::uint8_t* lower = upper + channels * w;
        const int weight_y = rows[y].m_weight;
        // El DIB va de abajo hacia arriba.
        std::uint8_t* dest = (std::uint8_t*)image_bits + (std::size_t)stride * (dest_h - 1 - offset_y - y) + dest_channels * offset_x;
        for (int x = 0; x < target_w; x++)
        {
            const int from = channels * columns[x].m_index;
            const int weight_x = columns[x].m_weight;
            std::uint8_t mixed[4];
            for (int c = 0; c < channels; c++)
            {
                const int top = upper[from + c] * (256 - weight_x) + upper[from + channels + c] * weight_x;
                const int bottom = lower[from + c] * (256 - weight_x) + lower[from + channels + c] * weight_x;
                mixed[c] = (std::uint8_t)((top * (256 - weight_y) + bottom * weight_y + (1 << 15)) >> 16);
            }
            convertPixel(mixed, channels, dest, dest_channels);
            dest += dest_channels;
        }
    }
}

// CelCam: cada cámara tiene su memoria y su cerrojo.
std::string objectName(int index, const char* what)
{
    return "CelCam/3/" + std::to_string(index) + "/" + what;
}

bool validIndex(int index)
{
    return 0 <= index && index < softcam::FrameBuffer::MaxCameras;
}

bool validChannels(int channels)
{
    return channels == 3 || channels == 4;
}

} //namespace


namespace softcam {


// CelCam: el «3» de los nombres (ver objectName) es por los cuadros con transparencia. Un filtro de una
// versión anterior que siga cargado en una app no los conoce y leería de menos: con otro nombre, no se engancha.
const uint8_t ProtocolVersion = 2;
// CelCam: la calidad más alta que ofrece el programa.
const uint16_t MaxWidth = 1920;
const uint16_t MaxHeight = 1080;


struct FrameBuffer::Header
{
    uint32_t    m_image_offset;
    uint16_t    m_width;
    uint16_t    m_height;
    float       m_framerate;
    uint32_t    m_channels; // 3 (BGR) o 4 (BGRA)
    uint8_t     m_is_active;
    uint8_t     m_connected_min_version; // 0 or 1 or 2
    uint8_t     m_watchdog_sender_heartbeat;
    uint8_t     m_watchdog_receiver_heartbeat;
    uint64_t    m_frame_counter;

    uint8_t*    imageData();
};


uint8_t* FrameBuffer::Header::imageData()
{
    uint8_t *image = reinterpret_cast<uint8_t*>(this) + m_image_offset;
    return image;
}


FrameBuffer FrameBuffer::create(
                        int             index,
                        int             width,
                        int             height,
                        float           framerate,
                        int             channels)
{
    FrameBuffer fb(objectName(index, "NamedMutex").c_str());

    if (!validIndex(index) || !validChannels(channels) || !checkDimensions(width, height))
    {
        return fb;
    }
    if (framerate < 0.0f)
    {
        return fb;
    }

    // CelCam: con lugar para la calidad más alta, el formato puede cambiar sin recrear la memoria.
    auto shmem_size = (std::max)(
            calcMemorySize((uint16_t)width, (uint16_t)height, channels),
            calcMemorySize(MaxWidth, MaxHeight, 4));
    fb.m_shmem = SharedMemory::create(objectName(index, "SharedMemory").c_str(), shmem_size);
    if (fb.m_shmem)
    {
        std::lock_guard<NamedMutex> lock(fb.m_mutex);

        auto frame = fb.header();
        frame->m_image_offset = sizeof(Header);
        frame->m_width = (uint16_t)width;
        frame->m_height = (uint16_t)height;
        frame->m_framerate = framerate;
        frame->m_channels = (uint32_t)channels;
        frame->m_is_active = 1;
        frame->m_connected_min_version = 0;
        frame->m_watchdog_sender_heartbeat = 0;
        frame->m_watchdog_receiver_heartbeat = 0;
        frame->m_frame_counter = 0;

        auto mutex = fb.m_mutex;
        fb.m_sender_watchdog = Watchdog::createHeartbeat(
            WATCHDOG_HEARTBEAT_INTERVAL,
            [mutex, frame]() mutable
            {
                std::lock_guard<NamedMutex> lock(mutex);
                frame->m_watchdog_sender_heartbeat += 1;
            });
        fb.m_receiver_watchdog = Watchdog::createMonitor(
            WATCHDOG_MONITOR_INTERVAL,
            WATCHDOG_TIMEOUT,
            [mutex, frame]() mutable
            {
                std::lock_guard<NamedMutex> lock(mutex);
                return frame->m_watchdog_receiver_heartbeat;
            });
    }
    return fb;
}

FrameBuffer FrameBuffer::open(int index)
{
    FrameBuffer fb(objectName(index, "NamedMutex").c_str());

    if (!validIndex(index))
    {
        return fb;
    }
    fb.m_shmem = SharedMemory::open(objectName(index, "SharedMemory").c_str());
    if (fb.m_shmem)
    {
        std::lock_guard<NamedMutex> lock(fb.m_mutex);

        auto size = fb.m_shmem.size();
        if (size < sizeof(Header))
        {
            fb.m_shmem = {};
            return fb;
        }
        auto frame = fb.header();
        if (!checkDimensions(frame->m_width, frame->m_height) ||
            !validChannels((int)frame->m_channels) ||
            frame->m_framerate < 0.0f)
        {
            fb.m_shmem = {};
            return fb;
        }
        uint32_t image_size = (uint32_t)frame->m_width * (uint32_t)frame->m_height * frame->m_channels;
        if (size <= frame->m_image_offset ||
            size - frame->m_image_offset < image_size)
        {
            fb.m_shmem = {};
            return fb;
        }

        auto mutex = fb.m_mutex;
        fb.m_sender_watchdog = Watchdog::createMonitor(
            WATCHDOG_MONITOR_INTERVAL,
            WATCHDOG_TIMEOUT,
            [mutex, frame]() mutable
            {
                std::lock_guard<NamedMutex> lock(mutex);
                return frame->m_watchdog_sender_heartbeat;
            });
        fb.m_receiver_watchdog = Watchdog::createHeartbeat(
            WATCHDOG_HEARTBEAT_INTERVAL,
            [mutex, frame]() mutable
            {
                std::lock_guard<NamedMutex> lock(mutex);
                frame->m_watchdog_receiver_heartbeat += 1;
            });
        if (0 == frame->m_connected_min_version ||
            ProtocolVersion <= frame->m_connected_min_version)
        {
            frame->m_connected_min_version = ProtocolVersion;
        }
        frame->m_watchdog_receiver_heartbeat += 1;
    }

    return fb;
}

FrameBuffer&
FrameBuffer::operator =(const FrameBuffer& fb)
{
    m_receiver_watchdog = {};
    m_sender_watchdog = {};
    m_shmem = {};
    m_shmem = fb.m_shmem;
    m_sender_watchdog = fb.m_sender_watchdog;
    m_receiver_watchdog = fb.m_receiver_watchdog;
    return *this;
}

void* FrameBuffer::handle() const
{
    return const_cast<void*>(m_shmem.get());
}

int FrameBuffer::width() const
{
    std::lock_guard<NamedMutex> lock(m_mutex);
    return m_shmem ? header()->m_width : 0;
}

int FrameBuffer::height() const
{
    std::lock_guard<NamedMutex> lock(m_mutex);
    return m_shmem ? header()->m_height : 0;
}

float FrameBuffer::framerate() const
{
    std::lock_guard<NamedMutex> lock(m_mutex);
    return m_shmem ? header()->m_framerate : 0.0f;
}

int FrameBuffer::channels() const
{
    std::lock_guard<NamedMutex> lock(m_mutex);
    return m_shmem ? (int)header()->m_channels : 3;
}

uint64_t FrameBuffer::frameCounter() const
{
    std::lock_guard<NamedMutex> lock(m_mutex);
    return m_shmem ? header()->m_frame_counter : 0;
}

bool FrameBuffer::active() const
{
    std::lock_guard<NamedMutex> lock(m_mutex);
    return m_shmem && header()->m_is_active;
}

bool FrameBuffer::connected() const
{
    std::lock_guard<NamedMutex> lock(m_mutex);
    if (m_shmem)
    {
        auto ver = header()->m_connected_min_version;
        if (0 == ver)
        {
            // No receivers connected
            return false;
        }
        if (1 == ver)
        {
            // At least one of the connected receivers is version 1.
            // Since receivers of version 1 don't have receiver-watchdog,
            // we won't know their disconnection.
            return true;
        }
        return m_receiver_watchdog.alive();
    }
    return false;
}

void FrameBuffer::deactivate()
{
    if (!m_shmem) return;
    std::lock_guard<NamedMutex> lock(m_mutex);
    header()->m_is_active = 0;
}

void FrameBuffer::write(const void* image_bits)
{
    if (!m_shmem) return;
    std::lock_guard<NamedMutex> lock(m_mutex);
    auto frame = header();
    std::memcpy(
            frame->imageData(),
            image_bits,
            (std::size_t)frame->m_channels * frame->m_width * frame->m_height);
    frame->m_frame_counter += 1;
}

bool FrameBuffer::setFormat(int width, int height, float framerate, int channels, const void* image_bits)
{
    if (!m_shmem || !checkDimensions(width, height) || !validChannels(channels) || framerate < 0.0f)
    {
        return false;
    }
    if (calcMemorySize((uint16_t)width, (uint16_t)height, channels) > m_shmem.size())
    {
        return false;
    }
    // Todo junto: quien lee nunca ve el tamaño nuevo con la imagen vieja.
    std::lock_guard<NamedMutex> lock(m_mutex);
    auto frame = header();
    frame->m_width = (uint16_t)width;
    frame->m_height = (uint16_t)height;
    frame->m_framerate = framerate;
    frame->m_channels = (uint32_t)channels;
    const std::size_t image_size = (std::size_t)channels * width * height;
    if (image_bits)
    {
        std::memcpy(frame->imageData(), image_bits, image_size);
    }
    else
    {
        std::memset(frame->imageData(), 0, image_size);
    }
    frame->m_frame_counter += 1;
    return true;
}

void FrameBuffer::transferToDIB(
                        void*                   image_bits,
                        int                     dest_width,
                        int                     dest_height,
                        int                     dest_channels,
                        std::vector<uint8_t>&   scratch,
                        uint64_t*               out_frame_counter)
{
    if (!m_shmem)
    {
        *out_frame_counter = 0;
        return;
    }
    int w = 0;
    int h = 0;
    int channels = 3;
    {
        std::lock_guard<NamedMutex> lock(m_mutex);

        auto frame = header();
        w = frame->m_width;
        h = frame->m_height;
        channels = (int)frame->m_channels;
        *out_frame_counter = frame->m_frame_counter;
        if (w == dest_width && h == dest_height)
        {
            copyToDIB(frame->imageData(), w, h, channels, image_bits, dest_channels);
            return;
        }
        // Escalar lleva su tiempo: se hace afuera para no frenar al que manda los cuadros.
        scratch.resize((std::size_t)channels * w * h);
        std::memcpy(scratch.data(), frame->imageData(), scratch.size());
    }
    scaleToDIB(scratch.data(), w, h, channels, image_bits, dest_width, dest_height, dest_channels);
}

bool FrameBuffer::waitForNewFrame(uint64_t frame_counter, float time_out)
{
    if (!m_shmem) return false;
    Timer timer;
    while (active() && m_sender_watchdog.alive())
    {
        if (frameCounter() > frame_counter)
        {
            return true;
        }
        Timer::sleep(0.001f);
        if (0.0f < time_out && time_out <= timer.get())
        {
            return true;
        }
    }
    return false;
}

void FrameBuffer::release()
{
    m_receiver_watchdog.stop();
    m_sender_watchdog.stop();
    m_shmem = SharedMemory{};
}

FrameBuffer::Header* FrameBuffer::header()
{
    return static_cast<Header*>(m_shmem.get());
}

const FrameBuffer::Header* FrameBuffer::header() const
{
    return static_cast<const Header*>(m_shmem.get());
}

bool FrameBuffer::checkDimensions(
                        int width,
                        int height)
{
    // We reject big numbers and numbers not multiple of four.
    // Too large pictures may exceed the 32-bit limit of DIBitmap and shared memory.
    // Sizes which are not multiple of four tend to cause problems in the subsequent
    // processes such as color conversion or compression in video applications.
    if (width < 1 || width > 16384 || width % 4 != 0 ||
        height < 1 || height > 16384 || height % 4 != 0)
    {
        return false;
    }
    return true;
}

uint32_t FrameBuffer::calcMemorySize(
                        uint16_t width,
                        uint16_t height,
                        int      channels)
{
    uint32_t header_size = sizeof(Header);
    uint32_t image_size = (uint32_t)width * height * channels;
    uint32_t shmem_size = header_size + image_size;
    return shmem_size;
}


} //namespace softcam

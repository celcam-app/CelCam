#include "SenderAPI.h"

#include <atomic>

#include "FrameBuffer.h"
#include "Misc.h"


namespace {

struct Camera
{
    softcam::FrameBuffer    m_frame_buffer;
    softcam::Timer          m_timer;
};

// CelCam: una por cada cámara del sistema.
std::atomic<Camera*>    s_cameras[softcam::FrameBuffer::MaxCameras];

// Nunca se lee la cámara antes de confirmar que sigue viva: el handle puede ser de una ya borrada.
Camera* findCamera(softcam::sender::CameraHandle camera)
{
    Camera* target = static_cast<Camera*>(camera);
    if (target)
    {
        for (auto& slot : s_cameras)
        {
            if (slot.load() == target)
            {
                return target;
            }
        }
    }
    return nullptr;
}

} //namespace


namespace softcam {
namespace sender {

CameraHandle    CreateCamera(int index, int width, int height, float framerate, int channels)
{
    if (index < 0 || index >= FrameBuffer::MaxCameras)
    {
        return nullptr;
    }
    if (auto fb = FrameBuffer::create(index, width, height, framerate, channels))
    {
        Camera* camera = new Camera{ fb, Timer() };
        Camera* expected = nullptr;
        if (s_cameras[index].compare_exchange_strong(expected, camera))
        {
            return camera;
        }
        delete camera;
    }
    return nullptr;
}

void            DeleteCamera(CameraHandle camera)
{
    Camera* target = static_cast<Camera*>(camera);
    if (!target)
    {
        return;
    }
    for (auto& slot : s_cameras)
    {
        Camera* expected = target;
        if (slot.compare_exchange_strong(expected, nullptr))
        {
            target->m_frame_buffer.deactivate();
            delete target;
            return;
        }
    }
}

void            SendFrame(CameraHandle camera, const void* image_bits)
{
    Camera* target = findCamera(camera);
    if (target && image_bits)
    {
        // CelCam: the frame rate is only what the camera announces to applications.
        // Frames come from a phone in real time, so they are never delayed here.
        target->m_frame_buffer.write(image_bits);
    }
}

bool            SetFormat(CameraHandle camera, int width, int height, float framerate, int channels, const void* image_bits)
{
    Camera* target = findCamera(camera);
    if (target)
    {
        return target->m_frame_buffer.setFormat(width, height, framerate, channels, image_bits);
    }
    return false;
}

bool            WaitForConnection(CameraHandle camera, float timeout)
{
    Camera* target = findCamera(camera);
    if (target)
    {
        Timer timer;
        while (!target->m_frame_buffer.connected())
        {
            if (0.0f < timeout && timeout <= timer.get())
            {
                return false;
            }
            Timer::sleep(0.001f);
        }
        return true;
    }
    return false;
}

bool            IsConnected(CameraHandle camera)
{
    Camera* target = findCamera(camera);
    if (target)
    {
        return target->m_frame_buffer.connected();
    }
    return false;
}

} //namespace sender
} //namespace softcam

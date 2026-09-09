#pragma once

#include <memory>

#include <QOpenGLFunctions>
#include <QOpenGLShaderProgram>
#include <QOpenGLWidget>
#include <QThread>
#include "cereal/visionipc/visionipc_client.h"
#include "selfdrive/camerad/cameras/camera_common.h"
#include "selfdrive/common/visionimg.h"
#include "selfdrive/ui/ui.h"

class CameraViewWidget : public QOpenGLWidget, protected QOpenGLFunctions {
  Q_OBJECT

public:
  using QOpenGLWidget::QOpenGLWidget;
  explicit CameraViewWidget(std::string stream_name, VisionStreamType stream_type, bool zoom, QWidget* parent = nullptr);
  ~CameraViewWidget();
  void setStreamType(VisionStreamType type) { stream_type = type; }
  void setBackgroundColor(const QColor &color) { bg = color; }

signals:
  void clicked();
  void vipcThreadConnected(VisionIpcClient *);
  void vipcThreadFrameReceived(VisionBuf *);

protected:
  void paintGL() override;
  void initializeGL() override;
  void resizeGL(int w, int h) override { updateFrameMat(w, h); }
  void showEvent(QShowEvent *event) override;
  void hideEvent(QHideEvent *event) override;
  void mouseReleaseEvent(QMouseEvent *event) override { emit clicked(); }
  virtual void updateFrameMat(int w, int h);
  void vipcThread();

  struct WaitFence {
    WaitFence() { sync = glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE, 0); }
    ~WaitFence() { glDeleteSync(sync); }
    void wait() { glWaitSync(sync, 0, GL_TIMEOUT_IGNORED); }
    GLsync sync = 0;
  };

  bool zoomed_view;
  std::mutex lock;
  int latest_texture_id = -1;
  // 2026-09-09: 카메라 프레임 N 개당 1 번만 화면을 다시 그린다(1 = 매 프레임).
  // NvgWindow 가 2 로 두어 EON 도로화면을 10fps 로 낮춘다. 운전자뷰 등 다른
  // CameraViewWidget 은 1 그대로.
  int frame_divider = 1;
  uint64_t frame_counter = 0;
  GLuint frame_vao, frame_vbo, frame_ibo;
  mat4 frame_mat;
  std::unique_ptr<EGLImageTexture> texture[UI_BUF_COUNT];
  std::unique_ptr<WaitFence> wait_fence;
  std::unique_ptr<QOpenGLShaderProgram> program;
  QColor bg = QColor("#000000");

  std::string stream_name;
  int stream_width = 0;
  int stream_height = 0;
  std::atomic<VisionStreamType> stream_type;
  QThread *vipc_thread = nullptr;

protected slots:
  void vipcConnected(VisionIpcClient *vipc_client);
};

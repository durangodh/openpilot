// QT_QPA_PLATFORM=offscreen ./test_web_screen [frame-output]
#include <cstdio>
#include <time.h>
#include <QApplication>
#include <QDataStream>
#include <QFile>
#include <QOpenGLWidget>
#include <QOpenGLFunctions>
#include <QTemporaryDir>
#include <QTimer>
#include <QWidget>
#include "selfdrive/ui/qt/web_screen.h"

class GLChild : public QOpenGLWidget, protected QOpenGLFunctions {
public:
  using QOpenGLWidget::QOpenGLWidget;
protected:
  void initializeGL() override { initializeOpenGLFunctions(); }
  void paintGL() override { glClearColor(.1f, .8f, .2f, 1.f); glClear(GL_COLOR_BUFFER_BIT); }
};

int main(int argc, char **argv) {
  QApplication app(argc, argv);
  QTemporaryDir directory;
  if (!directory.isValid()) return 1;
  QWidget window; window.resize(800, 400);
  window.setStyleSheet("background: rgb(20,60,120)");
  QWidget child(&window); child.setGeometry(0, 0, 400, 200);
  child.setStyleSheet("background: rgb(220,40,30)");
  GLChild *gl = nullptr;
  if (qEnvironmentVariableIsSet("TEST_WEB_SCREEN_GL")) {
    gl = new GLChild(&window); gl->setGeometry(400, 0, 400, 200);
  }
  window.show(); child.show(); if (gl) gl->show();
  WebScreenCapture capture(&window, directory.path());
  const QString frame_path = directory.path() + "/eon-web-screen.frame";
  QFile request(directory.path() + "/eon-web-screen.request");
  if (!request.open(QIODevice::WriteOnly)) return 2;
  timespec ts = {}; clock_gettime(CLOCK_MONOTONIC, &ts);
  request.write(QByteArray::number(qulonglong(ts.tv_sec) * 1000000000ULL + ts.tv_nsec)); request.close();
  int code = 0, heartbeats = 0;
  QTimer heartbeat;
  QObject::connect(&heartbeat, &QTimer::timeout, [&]() { ++heartbeats; }); heartbeat.start(20);
  QByteArray expired;
  QTimer::singleShot(1500, [&]() {
    QFile frame(frame_path);
    if (!frame.open(QIODevice::ReadOnly)) { code = 3; app.quit(); return; }
    const QByteArray raw = frame.readAll();
    QDataStream in(raw); in.setByteOrder(QDataStream::LittleEndian);
    char magic[4]; quint32 version, width, height, format, length; quint64 captured, finished;
    in.readRawData(magic, 4); in >> version >> captured >> finished >> width >> height >> format >> length;
    const QImage image = QImage::fromData(raw.mid(40));
    if (QByteArray(magic, 4) != "EWS1" || version != 1 || raw.size() != 40 + int(length) ||
        captured == 0 || finished < captured || width > 960 || height > 540 || image.isNull()) {
      code = 4; app.quit(); return;
    }
    const QColor red = image.pixelColor(image.width() / 4, image.height() / 4);
    const QColor blue = image.pixelColor(image.width() * 3 / 4, image.height() * 3 / 4);
    if (red.red() < 180 || red.green() > 80 || blue.blue() < 90 || blue.red() > 60) {
      code = 5; app.quit(); return;
    }
    if (gl) {
      const QColor green = image.pixelColor(image.width() * 3 / 4, image.height() / 4);
      if (!gl->isValid() || green.green() < 150 || green.red() > 70) { code = 8; app.quit(); return; }
    }
    if (argc > 1) {
      QFile out(argv[1]); if (!out.open(QIODevice::WriteOnly) || out.write(raw) != raw.size()) code = 6;
    }
  });
  QTimer::singleShot(3500, [&]() {
    QFile frame(frame_path); if (frame.open(QIODevice::ReadOnly)) expired = frame.readAll();
  });
  QTimer::singleShot(4600, [&]() {
    QFile frame(frame_path);
    if (!frame.open(QIODevice::ReadOnly) || expired.isEmpty() || expired != frame.readAll() || heartbeats < 100) code = 7;
    app.quit();
  });
  app.exec();
  if (code) std::fprintf(stderr, "Qt web capture check failed: %d\n", code);
  else std::puts("Qt root+child capture, encoding, event loop and demand expiry passed (GL checked when requested)");
  return code;
}

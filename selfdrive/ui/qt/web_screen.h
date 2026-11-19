#pragma once

#include <atomic>
#include <cstdint>
#include <thread>
#include <time.h>

#include <QBuffer>
#include <QDataStream>
#include <QDir>
#include <QFile>
#include <QImage>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSaveFile>
#include <QTimer>
#include <QWidget>

// Capture the Qt window, including its child widgets, on explicit web demand.
// Android screencap alone is not a dependable source of the Qt UI surface.
class WebScreenCapture : public QObject {
public:
  explicit WebScreenCapture(QWidget *window, const QString &directory = "/dev/shm")
      : QObject(window), window_(window), directory_(directory) {
    QDir().mkpath(directory_);
    QFile::remove(path("eon-web-screen.frame"));
    QFile::remove(path("eon-web-screen.error"));
    QObject::connect(&timer_, &QTimer::timeout, this, [this]() { tick(); });
    timer_.start(250);
  }

  ~WebScreenCapture() override {
    timer_.stop();
    if (worker_.joinable()) worker_.join();
  }

private:
  static uint64_t now_ns() {
    struct timespec ts = {};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return uint64_t(ts.tv_sec) * 1000000000ULL + uint64_t(ts.tv_nsec);
  }

  QString path(const char *name) const { return directory_ + "/" + name; }

  bool wanted(uint64_t now) const {
    QFile request(path("eon-web-screen.request"));
    if (!request.open(QIODevice::ReadOnly)) return false;
    const QByteArray text = request.read(33).trimmed();
    bool valid = false;
    const uint64_t requested = text.toULongLong(&valid);
    return valid && requested != 0 && requested <= now && now - requested < 3000000000ULL;
  }

  void error(const char *reason) const {
    QSaveFile file(path("eon-web-screen.error"));
    if (!file.open(QIODevice::WriteOnly)) return;
    file.setPermissions(QFileDevice::ReadOwner | QFileDevice::WriteOwner);
    const QJsonObject value{{"at_ns", QString::number(now_ns())}, {"error", QString::fromLatin1(reason)}};
    const QByteArray data = QJsonDocument(value).toJson(QJsonDocument::Compact);
    if (file.write(data) == data.size()) file.commit();
  }

  void tick() {
    const uint64_t now = now_ns();
    if (!wanted(now) || now - last_capture_ < 1000000000ULL || encoding_.load()) return;
    last_capture_ = now;
    if (!window_ || !window_->isVisible() || window_->size().isEmpty()) {
      error("ui_hidden");
      return;
    }
    // QPixmap / QWidget access must stay on the GUI thread. Only the copied
    // QImage's scaling, encoding and atomic file commit run in the worker.
    const QImage image = window_->grab().toImage();
    if (image.isNull()) {
      error("grab_failed");
      return;
    }
    if (worker_.joinable()) worker_.join();  // previous worker is already done
    encoding_.store(true);
    worker_ = std::thread([this, image, now]() {
      try {
        encode(image, now);
      } catch (...) {
        error("encode_failed");
      }
      encoding_.store(false);
    });
  }

  void encode(const QImage &source, uint64_t captured) const {
    const QImage image = source.scaled(960, 540, Qt::KeepAspectRatio, Qt::SmoothTransformation)
                               .convertToFormat(QImage::Format_RGB888);
    QByteArray bytes;
    QBuffer buffer(&bytes);
    buffer.open(QIODevice::WriteOnly);
    quint32 format = 1;
    if (!image.save(&buffer, "JPEG", 68)) {
      // EON Qt deployments can differ in their image plugins. PNG remains
      // lossless and viewable if the JPEG writer is unavailable.
      bytes.clear(); buffer.seek(0); format = 2;
      if (!image.save(&buffer, "PNG")) { error("encode_failed"); return; }
    }
    if (bytes.isEmpty() || bytes.size() > 2 * 1024 * 1024) { error("encode_failed"); return; }
    QSaveFile file(path("eon-web-screen.frame"));
    if (!file.open(QIODevice::WriteOnly)) { error("write_failed"); return; }
    file.setPermissions(QFileDevice::ReadOwner | QFileDevice::WriteOwner);
    QDataStream out(&file);
    out.setByteOrder(QDataStream::LittleEndian);
    out.writeRawData("EWS1", 4);
    out << quint32(1) << quint64(captured) << quint64(now_ns())
        << quint32(image.width()) << quint32(image.height()) << format << quint32(bytes.size());
    if (out.writeRawData(bytes.constData(), bytes.size()) != bytes.size() ||
        out.status() != QDataStream::Ok || !file.commit()) {
      error("write_failed");
      return;
    }
    QFile::remove(path("eon-web-screen.error"));
  }

  QWidget *window_;
  QString directory_;
  QTimer timer_;
  std::thread worker_;
  std::atomic<bool> encoding_{false};
  uint64_t last_capture_ = 0;
};

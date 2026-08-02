#include "selfdrive/ui/qt/onroad.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <initializer_list>
#include <string>

#include <QDebug>
#include <QSound>
#include <QMouseEvent>
#include <QDateTime>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QPainterPath>

#include "selfdrive/common/timing.h"
#include "selfdrive/ui/qt/util.h"
#ifdef ENABLE_MAPS
#include "selfdrive/ui/qt/maps/map.h"
#include "selfdrive/ui/qt/maps/map_helpers.h"
#endif

OnroadWindow::OnroadWindow(QWidget *parent) : QWidget(parent) {
  QVBoxLayout *main_layout  = new QVBoxLayout(this);
  main_layout->setMargin(bdr_s);
  QStackedLayout *stacked_layout = new QStackedLayout;
  stacked_layout->setStackingMode(QStackedLayout::StackAll);
  main_layout->addLayout(stacked_layout);

  QStackedLayout *road_view_layout = new QStackedLayout;
  road_view_layout->setStackingMode(QStackedLayout::StackAll);
  nvg = new NvgWindow(VISION_STREAM_RGB_ROAD, this);
  road_view_layout->addWidget(nvg);

  QWidget * split_wrapper = new QWidget;
  split = new QHBoxLayout(split_wrapper);
  split->setContentsMargins(0, 0, 0, 0);
  split->setSpacing(0);
  split->addLayout(road_view_layout);

  stacked_layout->addWidget(split_wrapper);

  alerts = new OnroadAlerts(this);
  alerts->setAttribute(Qt::WA_TransparentForMouseEvents, true);
  stacked_layout->addWidget(alerts);

  // setup stacking order
  alerts->raise();

  setAttribute(Qt::WA_OpaquePaintEvent);
  QObject::connect(uiState(), &UIState::uiUpdate, this, &OnroadWindow::updateState);
  QObject::connect(uiState(), &UIState::offroadTransition, this, &OnroadWindow::offroadTransition);

  // screen recoder - neokii

  record_timer = std::make_shared<QTimer>();
	QObject::connect(record_timer.get(), &QTimer::timeout, [=]() {
    if(recorder) {
      recorder->update_screen();
    }
  });
	record_timer->start(1000/UI_FREQ);

  QWidget* recorder_widget = new QWidget(this);
  QVBoxLayout * recorder_layout = new QVBoxLayout (recorder_widget);
  recorder_layout->setMargin(35);
  recorder = new ScreenRecoder(this);
  recorder_layout->addWidget(recorder);
  recorder_layout->setAlignment(recorder, Qt::AlignRight | Qt::AlignBottom);

  stacked_layout->addWidget(recorder_widget);
  recorder_widget->raise();
  alerts->raise();

}

void OnroadWindow::updateState(const UIState &s) {
  QColor bgColor = bg_colors[s.status];
  Alert alert = Alert::get(*(s.sm), s.scene.started_frame);
  if (s.sm->updated("controlsState") || !alert.equal({})) {
    if (alert.type == "controlsUnresponsive") {
      bgColor = bg_colors[STATUS_ALERT];
    } else if (alert.type == "controlsUnresponsivePermanent") {
      bgColor = bg_colors[STATUS_DISENGAGED];
    }
    alerts->updateAlert(alert, bgColor);
  }

  if (s.scene.map_on_left) {
    split->setDirection(QBoxLayout::LeftToRight);
  } else {
    split->setDirection(QBoxLayout::RightToLeft);
  }

  if (bg != bgColor) {
    // repaint border
    bg = bgColor;
    update();
  }
}

void OnroadWindow::mouseReleaseEvent(QMouseEvent* e) {

  QPoint endPos = e->pos();
  int dx = endPos.x() - startPos.x();
  int dy = endPos.y() - startPos.y();
  if(std::abs(dx) > 250 || std::abs(dy) > 200) {

    if(std::abs(dx) < std::abs(dy)) {

      if(dy < 0) { // upward
        Params().remove("CalibrationParams");
        Params().remove("LiveParameters");
        QTimer::singleShot(1500, []() {
          Params().putBool("SoftRestartTriggered", true);
        });

        QSound::play("../assets/sounds/reset_calibration.wav");
      }
      else { // downward
        QTimer::singleShot(500, []() {
          Params().putBool("SoftRestartTriggered", true);
        });
      }
    }
    else if(std::abs(dx) > std::abs(dy)) {
      if(dx < 0) { // right to left
        if(recorder)
          recorder->toggle();
      }
      else { // left to right
        if(recorder)
          recorder->toggle();
      }
    }

    return;
  }

    int tap_x = endPos.x();
    int tap_y = endPos.y();
    if (tap_x > 20 && tap_x < 200 &&
        tap_y > height() - 140 && tap_y < height() - 40) {
      int cur = std::atoi(Params().get("MyDrivingMode").c_str());
      if (cur < 1 || cur > 4) cur = 3;
      int next = cur % 4 + 1;   // 1→2→3→4→1
      Params().put("MyDrivingMode", std::to_string(next));
      return;
    }

  if (map != nullptr) {
    bool sidebarVisible = geometry().x() > 0;
    map->setVisible(!sidebarVisible && !map->isVisible());
  }

  // propagation event to parent(HomeWindow)
  QWidget::mouseReleaseEvent(e);
}

void OnroadWindow::mousePressEvent(QMouseEvent* e) {
  startPos = e->pos();
  //QWidget::mousePressEvent(e);
}

void OnroadWindow::offroadTransition(bool offroad) {
#ifdef ENABLE_MAPS
  if (!offroad) {
    if (map == nullptr && (uiState()->prime_type || !MAPBOX_TOKEN.isEmpty())) {
      MapWindow * m = new MapWindow(get_mapbox_settings());
      map = m;

      QObject::connect(uiState(), &UIState::offroadTransition, m, &MapWindow::offroadTransition);

      m->setFixedWidth(topWidget(this)->width() / 2);
      split->addWidget(m, 0, Qt::AlignRight);

      // Make map visible after adding to split
      m->offroadTransition(offroad);
    }
  }
#endif

  alerts->updateAlert({}, bg);

  // update stream type
  bool wide_cam = Hardware::TICI() && Params().getBool("EnableWideCamera");
  nvg->setStreamType(wide_cam ? VISION_STREAM_RGB_WIDE_ROAD : VISION_STREAM_RGB_ROAD);

  if(offroad && recorder) {
    recorder->stop(false);
  }

}

void OnroadWindow::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.fillRect(rect(), QColor(bg.red(), bg.green(), bg.blue(), 255));
}

// ***** onroad widgets *****

// OnroadAlerts
void OnroadAlerts::updateAlert(const Alert &a, const QColor &color) {
  if (!alert.equal(a) || color != bg) {
    alert = a;
    bg = color;
    update();
  }
}

void OnroadAlerts::paintEvent(QPaintEvent *event) {
  if (alert.size == cereal::ControlsState::AlertSize::NONE) {
    return;
  }
  static std::map<cereal::ControlsState::AlertSize, const int> alert_sizes = {
    {cereal::ControlsState::AlertSize::SMALL, 271},
    {cereal::ControlsState::AlertSize::MID, 420},
    {cereal::ControlsState::AlertSize::FULL, height()},
  };
  int h = alert_sizes[alert.size];
  QRect r = QRect(0, height() - h, width(), h);

  QPainter p(this);

  // draw background + gradient
  p.setPen(Qt::NoPen);
  p.setCompositionMode(QPainter::CompositionMode_SourceOver);

  p.setBrush(QBrush(bg));
  p.drawRect(r);

  QLinearGradient g(0, r.y(), 0, r.bottom());
  g.setColorAt(0, QColor::fromRgbF(0, 0, 0, 0.05));
  g.setColorAt(1, QColor::fromRgbF(0, 0, 0, 0.35));

  p.setCompositionMode(QPainter::CompositionMode_DestinationOver);
  p.setBrush(QBrush(g));
  p.fillRect(r, g);
  p.setCompositionMode(QPainter::CompositionMode_SourceOver);

  // text
  const QPoint c = r.center();
  p.setPen(QColor(0xff, 0xff, 0xff));
  p.setRenderHint(QPainter::TextAntialiasing);
  if (alert.size == cereal::ControlsState::AlertSize::SMALL) {
    configFont(p, "Open Sans", 74, "SemiBold");
    p.drawText(r, Qt::AlignCenter, alert.text1);
  } else if (alert.size == cereal::ControlsState::AlertSize::MID) {
    configFont(p, "Open Sans", 88, "Bold");
    p.drawText(QRect(0, c.y() - 125, width(), 150), Qt::AlignHCenter | Qt::AlignTop, alert.text1);
    configFont(p, "Open Sans", 66, "Regular");
    p.drawText(QRect(0, c.y() + 21, width(), 90), Qt::AlignHCenter, alert.text2);
  } else if (alert.size == cereal::ControlsState::AlertSize::FULL) {
    bool l = alert.text1.length() > 15;
    configFont(p, "Open Sans", l ? 132 : 177, "Bold");
    p.drawText(QRect(0, r.y() + (l ? 240 : 270), width(), 600), Qt::AlignHCenter | Qt::TextWordWrap, alert.text1);
    configFont(p, "Open Sans", 88, "Regular");
    p.drawText(QRect(0, r.height() - (l ? 361 : 420), width(), 300), Qt::AlignHCenter | Qt::TextWordWrap, alert.text2);
  }
}

// NvgWindow

NvgWindow::NvgWindow(VisionStreamType type, QWidget* parent) : last_update_params(0), fps_filter(UI_FREQ, 3, 1. / UI_FREQ), accel_filter(UI_FREQ, .5, 1. / UI_FREQ), CameraViewWidget("camerad", type, true, parent) {
}

void NvgWindow::initializeGL() {
  CameraViewWidget::initializeGL();
  qInfo() << "OpenGL version:" << QString((const char*)glGetString(GL_VERSION));
  qInfo() << "OpenGL vendor:" << QString((const char*)glGetString(GL_VENDOR));
  qInfo() << "OpenGL renderer:" << QString((const char*)glGetString(GL_RENDERER));
  qInfo() << "OpenGL language version:" << QString((const char*)glGetString(GL_SHADING_LANGUAGE_VERSION));

  prev_draw_t = millis_since_boot();
  setBackgroundColor(bg_colors[STATUS_DISENGAGED]);

  engage_img = loadPixmap("../assets/img_chffr_wheel.png", {img_size, img_size});
  experimental_img = loadPixmap("../assets/img_experimental.svg", {img_size - 5, img_size - 5});
	
  // neokii
  ic_brake = QPixmap("../assets/images/img_brake_disc.png").scaled(img_size, img_size, Qt::IgnoreAspectRatio, Qt::SmoothTransformation);
  ic_autohold_warning = QPixmap("../assets/images/img_autohold_warning.png").scaled(img_size, img_size, Qt::KeepAspectRatio, Qt::SmoothTransformation);
  ic_autohold_active = QPixmap("../assets/images/img_autohold_active.png").scaled(img_size, img_size, Qt::KeepAspectRatio, Qt::SmoothTransformation);
  ic_nda = QPixmap("../assets/images/img_nda.png");
  ic_hda = QPixmap("../assets/images/img_hda.png");
  ic_tire_pressure = QPixmap("../assets/images/img_tire_pressure.png");
  ic_turn_signal_l = QPixmap("../assets/images/turn_signal_l.png");
  ic_turn_signal_r = QPixmap("../assets/images/turn_signal_r.png");
  ic_satellite = QPixmap("../assets/images/satellite.png");

  ic_speed_bg = QPixmap("../assets/images/speed_bg.png");
}

void NvgWindow::updateState(const UIState &s) {	
  const SubMaster &sm = *(s.sm);
  const auto cs = sm["controlsState"].getControlsState();

  setProperty("status", s.status);

  // update engageability and DM icons at 2Hz
  if (sm.frame % (UI_FREQ / 2) == 0) {
    setProperty("engageable", cs.getEngageable() || cs.getEnabled());
    setProperty("experimentalMode", cs.getExperimentalMode());
  }

  // blind spot state sync
  auto car_state = sm["carState"].getCarState();
  setProperty("left_blindspot",  car_state.getLeftBlindspot());
  setProperty("right_blindspot", car_state.getRightBlindspot());

}

void NvgWindow::updateFrameMat(int w, int h) {
  CameraViewWidget::updateFrameMat(w, h);

  UIState *s = uiState();
  s->fb_w = w;
  s->fb_h = h;
  auto intrinsic_matrix = s->wide_camera ? ecam_intrinsic_matrix : fcam_intrinsic_matrix;
  float zoom = ZOOM / intrinsic_matrix.v[0];
  if (s->wide_camera) {
    zoom *= 0.5;
  }
  // Apply transformation such that video pixel coordinates match video
  // 1) Put (0, 0) in the middle of the video
  // 2) Apply same scaling as video
  // 3) Put (0, 0) in top left corner of video
  s->car_space_transform.reset();
  s->car_space_transform.translate(w / 2, h / 2 + y_offset)
      .scale(zoom, zoom)
      .translate(-intrinsic_matrix.v[2], -intrinsic_matrix.v[5]);
}

// carrot ui_draw_bsd 이식.
// 벽 폴리곤(앞으로 진행 + 뒤로 복귀 순서)을 두 점씩 끊어 사각 조각으로 그린다.
// 한 덩어리로 칠하지 않아 울타리 말뚝처럼 보인다.
void NvgWindow::drawBlindSpot(QPainter &painter, const line_vertices_data &vd, const QColor &color) {
  const int n = vd.cnt;
  if (n < 6) return;

  painter.save();
  painter.setPen(Qt::NoPen);
  painter.setBrush(color);

  // 앞쪽 진행 구간 v[i] 의 짝은 되돌아오는 구간 v[n-1-i] 이다.
  for (int i = 0; i + 1 < n / 2; i += 2) {
    QPointF quad[4] = {
      vd.v[i],
      vd.v[i + 1],
      vd.v[n - i - 2],
      vd.v[n - i - 1],
    };
    painter.drawPolygon(quad, 4);
  }
  painter.restore();
}

void NvgWindow::drawLaneLines(QPainter &painter, const UIState *s) {
  painter.save();
	
  const UIScene &scene = s->scene;
  SubMaster &sm = *(s->sm);
	
  // lanelines
  for (int i = 0; i < std::size(scene.lane_line_vertices); ++i) {
    painter.setBrush(QColor::fromRgbF(1.0, 1.0, 1.0, std::clamp<float>(scene.lane_line_probs[i], 0.0, 0.7)));
    painter.drawPolygon(scene.lane_line_vertices[i].v, scene.lane_line_vertices[i].cnt);
  }

  // road edges
  for (int i = 0; i < std::size(scene.road_edge_vertices); ++i) {
    painter.setBrush(QColor::fromRgbF(1.0, 0, 0, std::clamp<float>(1.0 - scene.road_edge_stds[i], 0.0, 1.0)));
    painter.drawPolygon(scene.road_edge_vertices[i].v, scene.road_edge_vertices[i].cnt);
  }

  // ── Blind Spot (carrot 방식) ──────────────────────────────────
  //   감지됐을 때만 노란 울타리를 세운다. 평상시에는 그리지 않는다.
  //   ShowBlindSpotAlways = 1 이면 감지 여부와 무관하게 흐리게 상시 표시한다.
  //   (BSD 신호가 안 들어오는지, 그리기가 안 되는지 구분할 때 유용)
  const QColor bsd_color(255, 215, 0, 150);
  const QColor bsd_idle(255, 255, 255, 40);
  if (left_blindspot)        drawBlindSpot(painter, scene.lane_barrier_vertices[0], bsd_color);
  else if (show_bsd_always)  drawBlindSpot(painter, scene.lane_barrier_vertices[0], bsd_idle);
  if (right_blindspot)       drawBlindSpot(painter, scene.lane_barrier_vertices[1], bsd_color);
  else if (show_bsd_always)  drawBlindSpot(painter, scene.lane_barrier_vertices[1], bsd_idle);
	
  // paint path
  QLinearGradient bg(0, height(), 0, height() / 4);
  float start_hue, end_hue;
  if (sm["controlsState"].getControlsState().getExperimentalMode()) {
    const auto &acceleration = sm["modelV2"].getModelV2().getAcceleration();
    float acceleration_future = 0;
    if (acceleration.getZ().size() > 16) {
      acceleration_future = acceleration.getX()[16];  // 2.5 seconds
    }
    if (scene.dynamic_lane_profile_status) {
      start_hue = 60;
      // speed up: 120, slow down: 0
      end_hue = fmax(fmin(start_hue + acceleration_future * 45, 148), 0);
    } else {
      start_hue = 240;
      // speed up: 300, slow down: 180
      end_hue = fmin(fmax(start_hue + acceleration_future * 45, 180), 328);
    }
    // FIXME: painter.drawPolygon can be slow if hue is not rounded
    end_hue = int(end_hue * 100 + 0.5) / 100;

    bg.setColorAt(0.0, QColor::fromHslF(start_hue / 360., 0.97, 0.56, 0.4));
    bg.setColorAt(0.5, QColor::fromHslF(end_hue / 360., 1.0, 0.68, 0.35));
    bg.setColorAt(1.0, QColor::fromHslF(end_hue / 360., 1.0, 0.68, 0.0));
  } else if (scene.dynamic_lane_profile_status) {
    bg.setColorAt(0.0, QColor::fromHslF(148 / 360., 0.94, 0.51, 0.4));
    bg.setColorAt(0.5, QColor::fromHslF(112 / 360., 1.0, 0.68, 0.35));
    bg.setColorAt(1.0, QColor::fromHslF(112 / 360., 1.0, 0.68, 0.0));
  } else {
    bg.setColorAt(0.0, QColor::fromHslF(197 / 360., 1.0, 0.55, 0.7));
    bg.setColorAt(0.5, QColor::fromHslF(200 / 360., 1.0, 0.70, 0.35));
    bg.setColorAt(1.0, QColor::fromHslF(200 / 360., 1.0, 0.70, 0.0));
  }
  painter.setBrush(bg);
  painter.drawPolygon(scene.track_vertices.v, scene.track_vertices.cnt);

  painter.restore();
}

void NvgWindow::paintGL() {
}

void NvgWindow::paintEvent(QPaintEvent *event) {


  UIState *s = uiState();
  const cereal::ModelDataV2::Reader &model = (*s->sm)["modelV2"].getModelV2();

  QPainter p(this);

  p.beginNativePainting();
  CameraViewWidget::paintGL();
  p.endNativePainting();

  if (s->worldObjectsVisible())
    drawHud(p, model);

  double cur_draw_t = millis_since_boot();
  double dt = cur_draw_t - prev_draw_t;
  double fps = fps_filter.update(1. / dt * 1000);
  if (fps < 15) {
    LOGW("slow frame rate: %.2f fps", fps);
  }
  prev_draw_t = cur_draw_t;
}

void NvgWindow::showEvent(QShowEvent *event) {
  CameraViewWidget::showEvent(event);

  auto now = millis_since_boot();
  if(now - last_update_params > 1000*5) {
    last_update_params = now;
    ui_update_params(uiState());
  }

  prev_draw_t = millis_since_boot();
}

void NvgWindow::drawText(QPainter &p, int x, int y, const QString &text, int alpha) {
  QFontMetrics fm(p.font());
  QRect init_rect = fm.boundingRect(text);
  QRect real_rect = fm.boundingRect(init_rect, 0, text);
  real_rect.moveCenter({x, y - real_rect.height() / 2});

  p.setPen(QColor(0xff, 0xff, 0xff, alpha));
  p.drawText(real_rect.x(), real_rect.bottom(), text);
}

void NvgWindow::drawTextWithColor(QPainter &p, int x, int y, const QString &text, QColor& color) {
  QFontMetrics fm(p.font());
  QRect init_rect = fm.boundingRect(text);
  QRect real_rect = fm.boundingRect(init_rect, 0, text);
  real_rect.moveCenter({x, y - real_rect.height() / 2});

  p.setPen(color);
  p.drawText(real_rect.x(), real_rect.bottom(), text);
}

void NvgWindow::drawIcon(QPainter &p, int x, int y, QPixmap &img, QBrush bg, float opacity, bool rotation, float angle) {
  p.save();
  p.setOpacity(opacity);
  p.setPen(Qt::NoPen);
  p.setBrush(bg);
  p.drawEllipse(x - radius / 2, y - radius / 2, radius, radius);

  if (rotation) {
    p.translate(x, y);
    p.rotate(-angle);           // 조향각만큼 회전 (반시계)
    QRect r = img.rect();
    r.moveCenter(QPoint(0, 0));
    p.drawPixmap(r, img);
  } else {
    p.drawPixmap(x - img_size / 2, y - img_size / 2, img_size, img_size, img);
  }

  p.restore();
}

void NvgWindow::drawText2(QPainter &p, int x, int y, int flags, const QString &text, const QColor& color) {
  QFontMetrics fm(p.font());
  QRect rect = fm.boundingRect(text);
  rect.adjust(-1, -1, 1, 1);
  p.setPen(color);
  p.drawText(QRect(x, y, rect.width()+1, rect.height()), flags, text);
}

void NvgWindow::drawHud(QPainter &p, const cereal::ModelDataV2::Reader &model) {

  p.setRenderHint(QPainter::Antialiasing);
  p.setPen(Qt::NoPen);
  p.setOpacity(1.);

  // Header gradient
  QLinearGradient bg(0, header_h - (header_h / 2.5), 0, header_h);
  bg.setColorAt(0, QColor::fromRgbF(0, 0, 0, 0.45));
  bg.setColorAt(1, QColor::fromRgbF(0, 0, 0, 0));
  p.fillRect(0, 0, width(), header_h, bg);

  UIState *s = uiState();

  const SubMaster &sm = *(s->sm);
  (void)sm;
  (void)model;

  drawLaneLines(p, s);

  drawCarrotLead(p);
  drawCarrotNavi(p);
  // --- replaced by CarrotPilot style HUD panel ---
  //drawMaxSpeed(p);
  //drawSpeed(p);
  drawCarrotHud(p);
  // ----------------------------------------------
  drawSpeedLimit(p);
  //drawSteer(p);      // 조향각 표시 제거
  //drawThermal(p);    // CPU/AMBIENT 온도 표시 제거
  //drawTurnSignals(p);
  //drawGpsStatus(p);   // GPS 위성 아이콘 표시 제거
  drawCarrotInfo(p);
  drawCarrotBottom(p);

  if(s->show_debug && width() > 1200)
    drawDebugText(p);

  // 하단 디버그 정보(TS/AO/SR/SAD/BUS/SCC) 표시 제거

  drawBottomIcons(p);

  drawTextAnim(p);   // 팝업 애니메이션은 항상 맨 위
}

static QJsonValue carrotFirstJsonValue(const QJsonObject &object,
                                       std::initializer_list<const char *> keys) {
  for (const char *key : keys) {
    if (object.contains(key)) return object.value(key);
  }
  return QJsonValue();
}

static bool carrotJsonBool(const QJsonObject &object,
                           std::initializer_list<const char *> keys) {
  const QJsonValue value = carrotFirstJsonValue(object, keys);
  if (value.isBool()) return value.toBool();
  if (value.isDouble()) return value.toInt() != 0;
  const QString text = value.toString().toLower();
  return text == "true" || text == "yes" || text == "active" || text == "recommended";
}

// The Tmap bridge has used both array and object lane payloads.  Normalize the
// common spellings here so old and new APK builds can share the same UI.
static void parseCarrotLanes(const QJsonValue &value, QVector<int> &types,
                             QVector<int> &active) {
  types.clear();
  active.clear();
  if (value.isNull() || value.isUndefined()) return;

  QJsonArray lanes;
  QJsonObject wrapper;
  if (value.isArray()) {
    lanes = value.toArray();
  } else if (value.isObject()) {
    wrapper = value.toObject();
    const QJsonValue lane_value = carrotFirstJsonValue(
      wrapper, {"lanes", "items", "lane_items", "laneItems", "data"});
    if (lane_value.isArray()) lanes = lane_value.toArray();
  }

  if (!lanes.isEmpty()) {
    const int count = std::min(8, lanes.size());
    for (int i = 0; i < count; ++i) {
      const QJsonValue lane = lanes.at(i);
      int type = 0;
      bool recommended = false;
      if (lane.isObject()) {
        const QJsonObject item = lane.toObject();
        type = carrotFirstJsonValue(item, {"turn_type", "turnType", "lane_type",
                                           "laneType", "direction", "type"}).toInt();
        recommended = carrotJsonBool(item, {"recommended", "is_recommended",
                                             "isRecommended", "active", "is_active",
                                             "isActive", "selected", "usable"});
      } else if (lane.isBool()) {
        recommended = lane.toBool();
      } else if (lane.isDouble()) {
        type = lane.toInt();
      }
      types.push_back(type);
      active.push_back(recommended ? 1 : 0);
    }
  } else if (!wrapper.isEmpty()) {
    const QJsonArray direction_values = carrotFirstJsonValue(
      wrapper, {"directions", "lane_types", "laneTypes", "lane_type"}).toArray();
    int count = carrotFirstJsonValue(
      wrapper, {"lane_count", "laneCount", "count", "total"}).toInt();
    if (count <= 0) count = direction_values.size();
    count = std::max(0, std::min(8, count));
    for (int i = 0; i < count; ++i) {
      types.push_back(i < direction_values.size() ? direction_values.at(i).toInt() : 0);
      active.push_back(0);
    }
  }

  if (types.isEmpty()) return;
  const QJsonArray active_values = carrotFirstJsonValue(
    wrapper, {"recommended_lanes", "recommendedLanes", "active_lanes", "activeLanes",
              "lane_available", "laneAvailable", "available", "recommended"}).toArray();
  if (active_values.size() == types.size()) {
    for (int i = 0; i < active_values.size(); ++i) {
      const QJsonValue flag = active_values.at(i);
      active[i] = flag.isBool() ? flag.toBool() : flag.toInt() != 0;
    }
  } else {
    for (const QJsonValue &index_value : active_values) {
      int index = index_value.toInt(-1);
      if (index >= 1 && index <= types.size()) --index;  // accept one-based indexes
      if (index >= 0 && index < active.size()) active[index] = 1;
    }
  }
}

void NvgWindow::updateCarrotNavi() {
  const uint64_t now = millis_since_boot();
  if (now - carrot_navi_last_read < 500) return;
  carrot_navi_last_read = now;

  QFile file("/dev/shm/carrot_navi_route.json");
  if (!file.open(QIODevice::ReadOnly)) return;
  QJsonParseError error;
  const QJsonDocument doc = QJsonDocument::fromJson(file.readAll(), &error);
  if (error.error != QJsonParseError::NoError || !doc.isObject()) return;

  const QJsonObject root = doc.object();
  carrot_navi_updated_at = root.value("updated_at_ms").toVariant().toULongLong();
  const QJsonObject stream_times = root.value("stream_updated_at_ms").toObject();
  carrot_navi_guidance_updated_at = stream_times.contains("guidance_current")
    ? stream_times.value("guidance_current").toVariant().toULongLong()
    : carrot_navi_updated_at;
  const QJsonObject vehicle = root.value("vehicle").toObject();
  carrot_navi_lat = vehicle.value("lat").toDouble();
  carrot_navi_lon = vehicle.value("lon").toDouble();
  carrot_navi_road = vehicle.value("road_name").toString();

  const QJsonObject guide = root.value("guidance_current").toObject();
  carrot_navi_instruction = guide.value("main_text").toString();
  if (carrot_navi_instruction.isEmpty()) carrot_navi_instruction = guide.value("road_name").toString();
  carrot_navi_distance = guide.contains("distance_m") ? guide.value("distance_m").toInt() : -1;
  carrot_navi_turn_type = guide.value("turn_type").toInt();

  const QJsonObject next_guide = root.value("guidance_next").toObject();
  carrot_navi_next_instruction = next_guide.value("main_text").toString();
  if (carrot_navi_next_instruction.isEmpty()) carrot_navi_next_instruction = next_guide.value("road_name").toString();
  carrot_navi_next_distance = next_guide.contains("distance_m") ? next_guide.value("distance_m").toInt() : -1;
  carrot_navi_next_turn_type = next_guide.value("turn_type").toInt();

  QVector<int> current_types, current_active, ahead_types, ahead_active;
  parseCarrotLanes(root.value("lane_current"), current_types, current_active);
  parseCarrotLanes(root.value("lane_ahead"), ahead_types, ahead_active);
  carrot_navi_lanes_ahead = current_types.isEmpty() && !ahead_types.isEmpty();
  carrot_navi_lane_types = carrot_navi_lanes_ahead ? ahead_types : current_types;
  carrot_navi_lane_active = carrot_navi_lanes_ahead ? ahead_active : current_active;

  const QJsonObject route = root.value("route").toObject();
  carrot_navi_remain_distance = route.contains("remain_distance_m") ? route.value("remain_distance_m").toInt() : -1;
  carrot_navi_remain_time = route.contains("remain_time_sec") ? route.value("remain_time_sec").toInt() : -1;
  const QJsonArray points = route.value("polyline").toArray();
  if (root.contains("route") && points.isEmpty()) {
    carrot_navi_route.clear();
  } else if (!points.isEmpty()) {
    QVector<QPointF> next;
    const int stride = std::max(1, (points.size() + 179) / 180);
    next.reserve(std::min(180, points.size()));
    for (int i = 0; i < points.size(); i += stride) {
      const QJsonValue value = points.at(i);
      const QJsonObject point = value.toObject();
      const double lat = point.value("lat").toDouble();
      const double lon = point.value("lon").toDouble();
      if (std::abs(lat) > 0.000001 && std::abs(lon) > 0.000001) next.push_back(QPointF(lon, lat));
    }
    if (!points.isEmpty() && (points.size() - 1) % stride != 0) {
      const QJsonObject point = points.last().toObject();
      const double lat = point.value("lat").toDouble();
      const double lon = point.value("lon").toDouble();
      if (std::abs(lat) > 0.000001 && std::abs(lon) > 0.000001) next.push_back(QPointF(lon, lat));
    }
    carrot_navi_route = next;
  }

  // ---- speed 스트림: 도로 제한속도 (내비 중 LIMIT 폴백용) ----
  carrot_navi_speed_limit = 0;
  const QJsonObject speed = root.value("speed").toObject();
  if (!speed.isEmpty()) {
    // 서버 키 형식(snake/camel) 불확실 → 후보 키 순차 시도 (도로 제한속도 우선)
    static const char *limit_keys[] = {
      "road_limit_kph", "limit_speed", "roadLimitKph",
      "section_speed_limit_kph", "sectionSpeedLimitKph",
      "sdi_speed_limit_kph", "sdiSpeedLimitKph"
    };
    for (const char *k : limit_keys) {
      if (speed.contains(k)) {
        const int v = speed.value(k).toInt();
        if (v > 0 && v <= 150) { carrot_navi_speed_limit = v; break; }
      }
    }
  }
}

static QString carrotDistanceText(int meters) {
  if (meters < 0) return QString();
  if (meters < 1000) return QString("%1 m").arg(meters);
  return QString("%1 km").arg(meters / 1000.0, 0, 'f', meters < 10000 ? 1 : 0);
}

enum class CarrotTurnDirection { STRAIGHT, LEFT, RIGHT, UTURN, SLIGHT_LEFT, SLIGHT_RIGHT, ARRIVE };

enum class CarrotAtcKind { NONE, TURN, FORK, UTURN, ROTARY };

static bool carrotAtcTypeIn(int type, std::initializer_list<int> types) {
  return std::find(types.begin(), types.end(), type) != types.end();
}

static CarrotAtcKind carrotAtcKind(int type, const QString &text, int *direction) {
  *direction = 0;
  if (carrotAtcTypeIn(type, {12, 16})) {
    *direction = -1;
    return CarrotAtcKind::TURN;
  }
  if (carrotAtcTypeIn(type, {13, 19})) {
    *direction = 1;
    return CarrotAtcKind::TURN;
  }
  if (carrotAtcTypeIn(type, {7, 17, 44, 75, 76, 102, 105, 112, 115, 118})) {
    *direction = -1;
    return CarrotAtcKind::FORK;
  }
  if (carrotAtcTypeIn(type, {6, 43, 73, 74, 101, 104, 111, 114, 117, 123, 124})) {
    *direction = 1;
    return CarrotAtcKind::FORK;
  }
  if (type == 14) {
    *direction = -1;
    return CarrotAtcKind::UTURN;
  }
  if (type >= 131 && type <= 142) return CarrotAtcKind::ROTARY;

  const QString lower = text.toLower();
  if (lower.contains(QString::fromUtf8("유턴")) || lower.contains("u-turn") || lower.contains("uturn")) {
    *direction = -1;
    return CarrotAtcKind::UTURN;
  }
  const bool fork = lower.contains(QString::fromUtf8("분기")) ||
                    lower.contains(QString::fromUtf8("진출")) || lower.contains("fork");
  if (lower.contains(QString::fromUtf8("좌회전")) || lower.contains(QString::fromUtf8("왼쪽")) || lower.contains("left")) {
    *direction = -1;
    return fork ? CarrotAtcKind::FORK : CarrotAtcKind::TURN;
  }
  if (lower.contains(QString::fromUtf8("우회전")) || lower.contains(QString::fromUtf8("오른쪽")) || lower.contains("right")) {
    *direction = 1;
    return fork ? CarrotAtcKind::FORK : CarrotAtcKind::TURN;
  }
  return CarrotAtcKind::NONE;
}

static CarrotTurnDirection carrotTurnDirection(int type, const QString &text) {
  const QString value = text.toLower();
  if (value.contains(QString::fromUtf8("유턴")) || value.contains("u-turn") || type == 14) return CarrotTurnDirection::UTURN;
  if (value.contains(QString::fromUtf8("목적지")) || value.contains(QString::fromUtf8("도착")) || type == 2) return CarrotTurnDirection::ARRIVE;
  if (value.contains(QString::fromUtf8("왼쪽 방향")) || value.contains(QString::fromUtf8("좌측 방향")) ||
      value.contains(QString::fromUtf8("비스듬히 왼쪽")) || type == 16 || type == 17) return CarrotTurnDirection::SLIGHT_LEFT;
  if (value.contains(QString::fromUtf8("오른쪽 방향")) || value.contains(QString::fromUtf8("우측 방향")) ||
      value.contains(QString::fromUtf8("비스듬히 오른쪽")) || type == 18 || type == 19) return CarrotTurnDirection::SLIGHT_RIGHT;
  if (value.contains(QString::fromUtf8("좌회전")) || value.contains(QString::fromUtf8("왼쪽")) || type == 12) return CarrotTurnDirection::LEFT;
  if (value.contains(QString::fromUtf8("우회전")) || value.contains(QString::fromUtf8("오른쪽")) || type == 13) return CarrotTurnDirection::RIGHT;
  return CarrotTurnDirection::STRAIGHT;
}

static void drawCarrotTurnArrow(QPainter &p, const QRect &box, int type, const QString &text,
                                const QColor &color, int width) {
  const CarrotTurnDirection direction = carrotTurnDirection(type, text);
  const QPointF c = box.center();
  const qreal s = std::min(box.width(), box.height()) * 0.34;
  QPainterPath path;
  if (direction == CarrotTurnDirection::ARRIVE) {
    p.setPen(QPen(color, width));
    p.setBrush(color);
    p.drawEllipse(c, s * 0.45, s * 0.45);
    p.setBrush(Qt::NoBrush);
    p.drawEllipse(c, s * 0.85, s * 0.85);
    return;
  } else if (direction == CarrotTurnDirection::UTURN) {
    path.moveTo(c.x() + s * 0.55, c.y() + s);
    path.lineTo(c.x() + s * 0.55, c.y() - s * 0.25);
    path.cubicTo(c.x() + s * 0.55, c.y() - s, c.x() - s * 0.65, c.y() - s, c.x() - s * 0.65, c.y() - s * 0.2);
    path.moveTo(c.x() - s, c.y() - s * 0.35);
    path.lineTo(c.x() - s * 0.65, c.y() + s * 0.05);
    path.lineTo(c.x() - s * 0.3, c.y() - s * 0.35);
  } else {
    qreal dx = 0.0;
    if (direction == CarrotTurnDirection::LEFT) dx = -s;
    else if (direction == CarrotTurnDirection::RIGHT) dx = s;
    else if (direction == CarrotTurnDirection::SLIGHT_LEFT) dx = -s * 0.7;
    else if (direction == CarrotTurnDirection::SLIGHT_RIGHT) dx = s * 0.7;
    path.moveTo(c.x(), c.y() + s);
    if (direction == CarrotTurnDirection::LEFT || direction == CarrotTurnDirection::RIGHT) {
      path.lineTo(c.x(), c.y() - s * 0.2);
      path.lineTo(c.x() + dx, c.y() - s * 0.2);
    } else {
      path.lineTo(c.x() + dx, c.y() - s);
    }
    const QPointF tip = path.currentPosition();
    const qreal side = dx < 0 ? 1.0 : (dx > 0 ? -1.0 : 0.0);
    if (dx == 0.0) {
      path.moveTo(tip.x() - s * 0.35, tip.y() + s * 0.4);
      path.lineTo(tip);
      path.lineTo(tip.x() + s * 0.35, tip.y() + s * 0.4);
    } else {
      path.moveTo(tip.x() + side * s * 0.05, tip.y() + s * 0.45);
      path.lineTo(tip);
      path.lineTo(tip.x() + side * s * 0.45, tip.y() + s * 0.05);
    }
  }
  p.setBrush(Qt::NoBrush);
  p.setPen(QPen(color, width, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
  p.drawPath(path);
}

void NvgWindow::drawCarrotNavi(QPainter &p) {
  updateCarrotNavi();
  const qint64 wall_now = QDateTime::currentMSecsSinceEpoch();
  if (carrot_navi_updated_at == 0 || wall_now - static_cast<qint64>(carrot_navi_updated_at) > 35000) return;
  const bool route_active = carrot_navi_route.size() >= 2 &&
                            (carrot_navi_remain_distance > 0 || carrot_navi_remain_time > 0);
  if (!route_active) return;

  p.save();
  p.setRenderHint(QPainter::Antialiasing);
  p.setRenderHint(QPainter::TextAntialiasing);
  // 우측 내비 패널
  const int panel_y = 285;                 // 휠 아이콘 아래로 이동
  const int panel_bottom = height() - 5;  // 좌측 HUD 하단과 동일, 상태바 위
  const int panel_h = panel_bottom - panel_y;

  // 내부 레이아웃
  const int panel_header_h = 118;
  const int next_h = 54;
  const int lane_h = 70;
  const int panel_footer_h = 56;

  const int gap1 = 10;
  const int gap2 = 8;
  const int gap3 = 7;
  const int bottom_margin = 14;

  // 남는 공간을 지도에 모두 사용
  const int map_h =
      panel_h
      - panel_header_h
      - next_h
      - lane_h
      - panel_footer_h
      - gap1
      - gap2
      - gap3
      - bottom_margin;

  const QRect panel(width() - 475, panel_y, 440, panel_h);

  const QRect header(
      panel.x(),
      panel.y(),
      panel.width(),
      panel_header_h);

  const QRect next_row(
      panel.x(),
      header.bottom() + 1,
      panel.width(),
      next_h);

  const QRect map_rect(
      panel.x() + 14,
      next_row.bottom() + gap1,
      panel.width() - 28,
      map_h);

  const QRect lane_row(
      panel.x() + 14,
      map_rect.bottom() + gap2,
      panel.width() - 28,
      lane_h);

  const QRect footer(
      panel.x() + 14,
      lane_row.bottom() + gap3,
      panel.width() - 28,
      panel_footer_h);
  p.setPen(QPen(QColor(255, 255, 255, 120), 2));
  p.setBrush(QColor(8, 14, 18, 225));
  p.drawRoundedRect(panel, 24, 24);

  // Tmap-style primary guidance: green surface and high-contrast white content.
  p.setPen(Qt::NoPen);
  p.setBrush(QColor(20, 126, 78));
  p.drawRoundedRect(header, 24, 24);
  p.drawRect(QRect(header.left(), header.bottom() - 24, header.width(), 25));

  QString title = carrot_navi_instruction;
  if (title.isEmpty()) title = carrot_navi_road;
  if (title.isEmpty()) title = QString::fromUtf8("경로 안내");
  const QRect current_icon(header.x() + 14, header.y() + 10, 94, 108);
  drawCarrotTurnArrow(p, current_icon, carrot_navi_turn_type, title, Qt::white, 11);
  const QString distance_text = carrotDistanceText(carrot_navi_distance);
  configFont(p, "Open Sans", 53, "Bold");
  p.setPen(Qt::white);
  p.drawText(QRect(header.x() + 118, header.y() + 6, header.width() - 132, 66),
             Qt::AlignLeft | Qt::AlignVCenter,
             distance_text.isEmpty() ? QString::fromUtf8("안내 중") : distance_text);
  configFont(p, "Open Sans", 31, "Bold");
  const QString road_text = QFontMetrics(p.font()).elidedText(title, Qt::ElideRight, header.width() - 142);
  p.drawText(QRect(header.x() + 118, header.y() + 70, header.width() - 132, 45),
             Qt::AlignLeft | Qt::AlignVCenter, road_text);

  // Secondary maneuver row uses a darker green and a smaller white arrow.
  p.setPen(Qt::NoPen);
  p.setBrush(QColor(10, 82, 50));
  p.drawRect(next_row);
  drawCarrotTurnArrow(p, QRect(next_row.x() + 14, next_row.y() + 7, 44, 44),
                      carrot_navi_next_turn_type, carrot_navi_next_instruction,
                      Qt::white, 5);
  QString next_text = carrotDistanceText(carrot_navi_next_distance);
  if (!carrot_navi_next_instruction.isEmpty()) {
    next_text += (next_text.isEmpty() ? QString() : QString("  ·  ")) + carrot_navi_next_instruction;
  }
  if (next_text.isEmpty()) next_text = QString::fromUtf8("다음 안내 대기 중");
  configFont(p, "Open Sans", 27, "Bold");
  p.setPen(Qt::white);
  p.drawText(QRect(next_row.x() + 68, next_row.y(), next_row.width() - 80, next_row.height()),
             Qt::AlignLeft | Qt::AlignVCenter,
             QFontMetrics(p.font()).elidedText(next_text, Qt::ElideRight, next_row.width() - 88));

  // Lightweight schematic map background: a few fixed vector shapes only.
  QPainterPath map_clip;
  map_clip.addRoundedRect(map_rect, 14, 14);
  p.save();
  p.setClipPath(map_clip);
  p.setPen(Qt::NoPen);
  p.setBrush(QColor(34, 44, 51));
  p.drawRect(map_rect);
  p.setBrush(QColor(35, 72, 58));
  p.drawRoundedRect(QRect(map_rect.x() + 20, map_rect.y() + 18, 105, 67), 12, 12);
  p.drawRoundedRect(QRect(map_rect.right() - 115, map_rect.bottom() - 78, 96, 59), 12, 12);
  QPainterPath water;
  water.moveTo(map_rect.left() - 10, map_rect.y() + 205);
  water.cubicTo(map_rect.x() + 100, map_rect.y() + 150, map_rect.x() + 250, map_rect.y() + 245,
                map_rect.right() + 10, map_rect.y() + 185);
  water.lineTo(map_rect.right() + 10, map_rect.bottom() + 20);
  water.lineTo(map_rect.left() - 10, map_rect.bottom() + 20);
  water.closeSubpath();
  p.setBrush(QColor(38, 75, 96));
  p.drawPath(water);
  p.setBrush(QColor(55, 62, 68));
  for (int i = 0; i < 6; ++i) {
    const int bx = map_rect.x() + 24 + (i % 3) * 113;
    const int by = map_rect.y() + 99 + (i / 3) * 58;
    p.drawRoundedRect(QRect(bx, by, 66 + (i % 2) * 18, 30), 4, 4);
  }
  QPainterPath road_a, road_b, road_c;
  road_a.moveTo(map_rect.left() - 10, map_rect.y() + 85);
  road_a.cubicTo(map_rect.x() + 115, map_rect.y() + 135, map_rect.x() + 260, map_rect.y() + 30,
                 map_rect.right() + 10, map_rect.y() + 74);
  road_b.moveTo(map_rect.x() + 82, map_rect.top() - 10);
  road_b.cubicTo(map_rect.x() + 105, map_rect.y() + 105, map_rect.x() + 42, map_rect.y() + 205,
                 map_rect.x() + 142, map_rect.bottom() + 10);
  road_c.moveTo(map_rect.x() + 274, map_rect.top() - 10);
  road_c.cubicTo(map_rect.x() + 232, map_rect.y() + 78, map_rect.x() + 350, map_rect.y() + 155,
                 map_rect.x() + 306, map_rect.bottom() + 10);
  p.setBrush(Qt::NoBrush);
  p.setPen(QPen(QColor(18, 23, 27, 130), 13, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
  p.drawPath(road_a); p.drawPath(road_b); p.drawPath(road_c);
  p.setPen(QPen(QColor(104, 110, 114), 7, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
  p.drawPath(road_a); p.drawPath(road_b); p.drawPath(road_c);

  if (carrot_navi_route.size() >= 2) {
    double min_lon = carrot_navi_route[0].x(), max_lon = min_lon;
    double min_lat = carrot_navi_route[0].y(), max_lat = min_lat;
    for (const QPointF &point : carrot_navi_route) {
      min_lon = std::min(min_lon, point.x()); max_lon = std::max(max_lon, point.x());
      min_lat = std::min(min_lat, point.y()); max_lat = std::max(max_lat, point.y());
    }
    const bool has_car = std::abs(carrot_navi_lat) > 0.000001 && std::abs(carrot_navi_lon) > 0.000001;
    if (has_car && carrot_navi_lon > min_lon - 0.02 && carrot_navi_lon < max_lon + 0.02 &&
        carrot_navi_lat > min_lat - 0.02 && carrot_navi_lat < max_lat + 0.02) {
      min_lon = std::min(min_lon, carrot_navi_lon); max_lon = std::max(max_lon, carrot_navi_lon);
      min_lat = std::min(min_lat, carrot_navi_lat); max_lat = std::max(max_lat, carrot_navi_lat);
    }
    const double lon_span = std::max(0.00001, max_lon - min_lon);
    const double lat_span = std::max(0.00001, max_lat - min_lat);
    const double scale = std::min((map_rect.width() - 28) / lon_span, (map_rect.height() - 28) / lat_span);
    const double left = map_rect.center().x() - lon_span * scale / 2.0;
    const double top = map_rect.center().y() - lat_span * scale / 2.0;
    const auto project = [&](double lon, double lat) {
      return QPointF(left + (lon - min_lon) * scale, top + (max_lat - lat) * scale);
    };

    int car_idx = 0;
    if (has_car) {
      double best = 1e18;
      for (int i = 0; i < carrot_navi_route.size(); ++i) {
        const double dlon = carrot_navi_route[i].x() - carrot_navi_lon;
        const double dlat = carrot_navi_route[i].y() - carrot_navi_lat;
        const double d = dlon * dlon + dlat * dlat;
        if (d < best) { best = d; car_idx = i; }
      }
    }
    const auto smooth_path = [&](int first, int last) {
      QPainterPath path;
      if (first < 0 || last < first || last >= carrot_navi_route.size()) return path;
      path.moveTo(project(carrot_navi_route[first].x(), carrot_navi_route[first].y()));
      for (int i = first + 1; i < last; ++i) {
        const QPointF point = project(carrot_navi_route[i].x(), carrot_navi_route[i].y());
        const QPointF next = project(carrot_navi_route[i + 1].x(), carrot_navi_route[i + 1].y());
        path.quadTo(point, (point + next) / 2.0);
      }
      if (last > first) path.lineTo(project(carrot_navi_route[last].x(), carrot_navi_route[last].y()));
      return path;
    };

    const QPainterPath full_path = smooth_path(0, carrot_navi_route.size() - 1);
    const QPainterPath remain_path = smooth_path(car_idx, carrot_navi_route.size() - 1);
    p.setBrush(Qt::NoBrush);
    p.setPen(QPen(QColor(0, 0, 0, 185), 20, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
    p.drawPath(full_path);
    if (car_idx > 0) {
      p.setPen(QPen(QColor(105, 112, 120), 10, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
      p.drawPath(smooth_path(0, car_idx));
    }
    p.setPen(QPen(QColor(14, 70, 128), 15, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
    p.drawPath(remain_path);
    p.setPen(QPen(QColor(27, 143, 255), 10, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
    p.drawPath(remain_path);
    p.setPen(QPen(QColor(112, 207, 255), 3, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
    p.drawPath(remain_path);

    const QPointF destination = project(carrot_navi_route.last().x(), carrot_navi_route.last().y());
    p.setPen(QPen(Qt::white, 3)); p.setBrush(QColor(238, 74, 72));
    p.drawEllipse(destination, 11, 11);
    if (has_car) {
      const QPointF car = project(carrot_navi_lon, carrot_navi_lat);
      const int next_index = std::min(car_idx + 1, carrot_navi_route.size() - 1);
      const QPointF ahead = project(carrot_navi_route[next_index].x(), carrot_navi_route[next_index].y());
      p.save();
      p.translate(car);
      p.rotate(atan2(ahead.y() - car.y(), ahead.x() - car.x()) * 57.29577951);
      QPainterPath car_arrow;
      car_arrow.moveTo(17, 0); car_arrow.lineTo(-10, -12); car_arrow.lineTo(-4, 0);
      car_arrow.lineTo(-10, 12); car_arrow.closeSubpath();
      p.setPen(QPen(Qt::white, 2)); p.setBrush(QColor(26, 190, 104));
      p.drawPath(car_arrow);
      p.restore();
    }
  }
  p.restore();

  // Lane guidance: recommended lanes are blue, all others remain neutral gray.
  p.setPen(Qt::NoPen);
  p.setBrush(QColor(18, 26, 32, 235));
  p.drawRoundedRect(lane_row, 12, 12);
  configFont(p, "Open Sans", 18, "Bold");
  p.setPen(QColor(190, 202, 210));
  p.drawText(QRect(lane_row.x() + 10, lane_row.y() + 2, 92, 22), Qt::AlignLeft | Qt::AlignVCenter,
             carrot_navi_lanes_ahead ? QString::fromUtf8("다음 차선") : QString::fromUtf8("차선 안내"));
  const int lane_count = std::min(8, carrot_navi_lane_types.size());
  if (lane_count > 0) {
    const int gap = 5;
    const int area_x = lane_row.x() + 96;
    const int area_w = lane_row.width() - 106;
    const int lane_w = std::max(30, (area_w - gap * (lane_count - 1)) / lane_count);
    for (int i = 0; i < lane_count; ++i) {
      const bool active = i < carrot_navi_lane_active.size() && carrot_navi_lane_active[i] != 0;
      const QRect lane_box(area_x + i * (lane_w + gap), lane_row.y() + 9, lane_w, 58);
      p.setPen(Qt::NoPen);
      p.setBrush(active ? QColor(30, 137, 255) : QColor(82, 91, 100));
      p.drawRoundedRect(lane_box, 8, 8);
      drawCarrotTurnArrow(p, lane_box.adjusted(4, 6, -4, -6), carrot_navi_lane_types[i],
                          QString(), Qt::white, 4);
    }
  } else {
    configFont(p, "Open Sans", 23, "Regular");
    p.setPen(QColor(130, 144, 154));
    p.drawText(QRect(lane_row.x() + 96, lane_row.y(), lane_row.width() - 108, lane_row.height()),
               Qt::AlignCenter, QString::fromUtf8("차선 정보 대기 중"));
  }

  // Remaining distance and ETA stay visible in a dedicated bottom row.
  p.setPen(Qt::NoPen);
  p.setBrush(QColor(13, 22, 27, 245));
  p.drawRoundedRect(footer, 12, 12);
  const QString remain_text = carrotDistanceText(carrot_navi_remain_distance);
  const QString eta_text = carrot_navi_remain_time > 0
    ? QDateTime::currentDateTime().addSecs(carrot_navi_remain_time).toString("HH:mm") : QString("--:--");
  configFont(p, "Open Sans", 25, "Bold");
  p.setPen(Qt::white);
  p.drawText(QRect(footer.x() + 14, footer.y(), footer.width() / 2 - 14, footer.height()),
             Qt::AlignLeft | Qt::AlignVCenter,
             QString::fromUtf8("남은 거리 %1").arg(remain_text.isEmpty() ? QString("--") : remain_text));
  p.setPen(QColor(108, 225, 166));
  p.drawText(QRect(footer.center().x(), footer.y(), footer.width() / 2 - 14, footer.height()),
             Qt::AlignRight | Qt::AlignVCenter, QString::fromUtf8("도착 %1").arg(eta_text));
  p.restore();
}

static const QColor get_tpms_color(float tpms) {
    if(tpms < 5 || tpms > 60) // N/A
        return QColor(255, 255, 255, 220);
    if(tpms < 31)
        return QColor(255, 90, 90, 220);
    return QColor(255, 255, 255, 220);
}

static const QString get_tpms_text(float tpms) {
    if(tpms < 5 || tpms > 60)
        return "";

    char str[32];
    snprintf(str, sizeof(str), "%.0f", round(tpms));
    return QString(str);
}

void NvgWindow::drawBottomIcons(QPainter &p) {
  p.save();
  const SubMaster &sm = *(uiState()->sm);
  auto car_state = sm["carState"].getCarState();
  auto scc_smoother = sm["carControl"].getCarControl().getSccSmoother();

  // tire pressure  --- carrot hud panel과 위치가 겹쳐서 비활성화 (필요하면 if(false) -> if(true))
  if (false) {
    const int w = 58;
    const int h = 126;
    const int x = 110;
    const int y = height() - h - 85;

    auto tpms = car_state.getTpms();
    const float fl = tpms.getFl();
    const float fr = tpms.getFr();
    const float rl = tpms.getRl();
    const float rr = tpms.getRr();

    p.setOpacity(0.8);
    p.drawPixmap(x, y, w, h, ic_tire_pressure);

    configFont(p, "Open Sans", 38, "Bold");

    QFontMetrics fm(p.font());
    QRect rcFont = fm.boundingRect("9");

    int center_x = x + 3;
    int center_y = y + h/2;
    const int marginX = (int)(rcFont.width() * 2.7f);
    const int marginY = (int)((h/2 - rcFont.height()) * 0.7f);

    drawText2(p, center_x-marginX, center_y-marginY-rcFont.height(), Qt::AlignRight, get_tpms_text(fl), get_tpms_color(fl));
    drawText2(p, center_x+marginX, center_y-marginY-rcFont.height(), Qt::AlignLeft, get_tpms_text(fr), get_tpms_color(fr));
    drawText2(p, center_x-marginX, center_y+marginY, Qt::AlignRight, get_tpms_text(rl), get_tpms_color(rl));
    drawText2(p, center_x+marginX, center_y+marginY, Qt::AlignLeft, get_tpms_text(rr), get_tpms_color(rr));
  }

  // cruise gap / brake / auto hold --- carrot hud panel 로 대체되어 제거함
  (void)scc_smoother;
  (void)car_state;

  // 현재 시간/날짜 표시 (carrot.cc :: drawDateTime 과 동일 위치/크기)
  if (show_datetime > 0) {
    const int dt_x = 170;
    const int dt_y = 185;   // 상단 정보줄과 겹치지 않도록 내림 (org: 120)
    QDateTime now = QDateTime::currentDateTime();

    p.setOpacity(1.0);

    if (show_datetime == 1 || show_datetime == 2) {
      ctText(p, dt_x, dt_y, now.toString("HH:mm"), 100, QColor(255, 255, 255, 255), true, true);
    }
    if (show_datetime == 1 || show_datetime == 3) {
      // tm_wday 순서와 맞추기 위해 일요일부터
      static const char *weekdays_ko[] = {"일", "월", "화", "수", "목", "금", "토"};
      int wday = now.date().dayOfWeek() % 7;   // Qt: 1=월 ... 7=일  ->  0=일 ... 6=토
      QString date_str = now.toString("MM-dd") + "(" +
                         QString::fromUtf8(weekdays_ko[wday]) + ")";
      ctText(p, dt_x, dt_y + 70, date_str, 60, QColor(255, 255, 255, 255), true, true);
    }
  }

  // engage-ability icon
  {
    float steer_angle = sm["carState"].getCarState().getSteeringAngleDeg();
    QColor engageBgColor = bg_colors[uiState()->status];
    engageBgColor.setAlpha(166);
    drawIcon(p, rect().right() - radius / 2 - bdr_s * 2, radius / 2 + int(bdr_s * 1.5) + 45,
             experimentalMode ? experimental_img : engage_img,
             engageBgColor, 1.0,
             true,
             steer_angle);
  }

  p.restore();
}

#define CT_WHITE        QColor(255, 255, 255, 255)
#define CT_BLACK_A(a)   QColor(0, 0, 0, a)
#define CT_GREEN        QColor(0, 203, 0, 255)
#define CT_GREEN_A(a)   QColor(0, 203, 0, a)
#define CT_OCHRE        QColor(218, 111, 37, 255)
#define CT_ORANGE_A(a)  QColor(255, 175, 3, a)
#define CT_RED_A(a)     QColor(201, 34, 49, a)
#define CT_YELLOW_A(a)  QColor(218, 202, 37, a)
#define CT_BLUE_A(a)    QColor(0, 0, 255, a)
#define CT_GREY_A(a)    QColor(191, 191, 191, a)
#define CT_WHITE_A(a)   QColor(255, 255, 255, a)

void NvgWindow::ctRect(QPainter &p, const QRect &r, const QColor &fill, int corner,
                       int borderWidth, const QColor &borderColor) {
  p.save();
  p.setRenderHint(QPainter::Antialiasing);
  if (borderWidth > 0) p.setPen(QPen(borderColor, borderWidth));
  else                 p.setPen(Qt::NoPen);
  p.setBrush(QBrush(fill));
  p.drawRoundedRect(r, corner, corner);
  p.restore();
}

// nanovg 의 NVG_ALIGN_CENTER | NVG_ALIGN_BOTTOM 과 동일하게 동작:
// x = 가로 중심, y = 글자 하단
void NvgWindow::ctText(QPainter &p, int x, int y, const QString &text, int size,
                       const QColor &color, bool bold, bool shadow) {
  if (text.isEmpty()) return;
  configFont(p, "Open Sans", size, bold ? "Bold" : "Regular");
  const int h = (int)(size * 1.55f);
  QRect r(x - 700, y - h, 1400, h);
  if (shadow) {
    p.setPen(CT_BLACK_A(200));
    p.drawText(r.translated(3, 4), Qt::AlignHCenter | Qt::AlignBottom, text);
  }
  p.setPen(color);
  p.drawText(r, Qt::AlignHCenter | Qt::AlignBottom, text);
}

void NvgWindow::ctTextIn(QPainter &p, const QRect &box, const QString &text, int size,
                         const QColor &color, bool bold) {
  if (text.isEmpty()) return;
  configFont(p, "Open Sans", size, bold ? "Bold" : "Regular");
  QFontMetrics fm(p.font());

  int cap = fm.capHeight();
  if (cap <= 0) cap = (int)(fm.ascent() * 0.72f);   // 폰트가 capHeight 를 못주면 근사

  int w = fm.boundingRect(text).width();
  int baseline = box.center().y() + cap / 2;

  p.setPen(color);
  p.drawText(box.x() + (box.width() - w) / 2, baseline, text);
}

void NvgWindow::drawCarrotInfo(QPainter &p) {
  p.save();

  const SubMaster &sm = *(uiState()->sm);
  const auto car_params      = sm["carParams"].getCarParams();
  const auto controls_state  = sm["controlsState"].getControlsState();
  const auto live_params     = sm["liveParameters"].getLiveParameters();
  const auto torque_state    = controls_state.getLateralControlState().getTorqueState();

  // ---- 좌상단 : wifi IP / SCC ----
  QString ip_head = QString::fromUtf8(sm["deviceState"].getDeviceState().getWifiIpAddress().cStr());
  if (ip_head.isEmpty()) ip_head = "--";

  int scc_bus = (int)car_params.getSccBus();
  QString scc_num  = (scc_bus < 0) ? QString("none") : QString::number(scc_bus);
  QString scc_tail = QString(car_params.getHasScc13() ? "+13" : "") +
                     QString(car_params.getHasScc14() ? "+14" : "");
  QString left_head = ip_head + "  SCC ";

  // ---- 우상단 : 토크값 / SR ----
  QString right_str;
  right_str.sprintf("LT(%.2f/%.3f)  SR(%.2f/%.2f)",
                    torque_state.getLatAccelFactor(),
                    torque_state.getFriction(),
                    controls_state.getSteerRatio(),
                    live_params.getSteerRatio());

  configFont(p, "Open Sans", 34, "Regular");
  QRect line(35, 12, width() - 55, 48);   // 상태바(bdr_s=20) 안쪽

  // 우상단은 한 덩어리
  p.setPen(QColor(0xff, 0xff, 0xff, 200));
  p.drawText(line, Qt::AlignRight | Qt::AlignVCenter, right_str);

  // 좌상단 : 차량명은 보통 굵기, SCC 부분은 전부 흰색 + 굵게
  {
    QFontMetrics fm_r(p.font());
    int cap = fm_r.capHeight();
    if (cap <= 0) cap = (int)(fm_r.ascent() * 0.72f);
    int base_y = line.center().y() + cap / 2;
    int tx = line.x();

    QString name_part = left_head;
    name_part.chop(4);                          // 뒤쪽 "SCC " 분리

    p.setPen(QColor(0xff, 0xff, 0xff, 200));
    p.drawText(tx, base_y, name_part);
    tx += fm_r.horizontalAdvance(name_part);

    // "SCC" 라벨 : 흰색 굵은 글씨
    configFont(p, "Open Sans", 34, "Bold");
    QFontMetrics fm_b(p.font());
    p.setPen(QColor(255, 255, 255, 255));
    p.drawText(tx, base_y, "SCC ");
    tx += fm_b.horizontalAdvance("SCC ");

    // 번호 : 빨간 배지 + 흰 글씨
    {
      int num_w = fm_b.horizontalAdvance(scc_num);
      QRect badge(tx, line.center().y() - 21, num_w + 22, 42);
      ctRect(p, badge, QColor(190, 0, 0, 255), 8);
      ctTextIn(p, badge, scc_num, 34, QColor(255, 255, 255, 255));
      tx += badge.width() + 6;
    }

    // 부가표기(+13/+14) : 흰색 굵은 글씨
    if (!scc_tail.isEmpty()) {
      configFont(p, "Open Sans", 34, "Bold");
      p.setPen(QColor(255, 255, 255, 255));
      p.drawText(tx, base_y, scc_tail);
    }

    configFont(p, "Open Sans", 34, "Regular");  // 이후 그리기용으로 원복
  }

  p.restore();
}

void NvgWindow::drawCarrotBottom(QPainter &p) {
  p.save();

  const SubMaster &sm = *(uiState()->sm);

  const int line_y = height() - 62;
  const int line_h = 48;

  QString lat_debug = QString::fromUtf8(
      sm["lateralPlan"].getLateralPlan().getLatDebugText().cStr());
  {
    int off = lat_debug.indexOf("offset", 0, Qt::CaseInsensitive);
    if (off >= 0) {
      int sep = lat_debug.lastIndexOf('|', off);
      lat_debug = (sep >= 0 ? lat_debug.left(sep) : lat_debug.left(off)).trimmed();
    }
  }
  if (!lat_debug.isEmpty()) {
    int right_limit = width() - 20;
    int left_limit = 240;
    int avail = right_limit - left_limit;

    if (avail > 100) {
      int fs = 34;
      for (; fs > 22; fs -= 2) {
        configFont(p, "Open Sans", fs, "Regular");
        if (QFontMetrics(p.font()).boundingRect(lat_debug).width() <= avail) break;
      }
      p.setPen(QColor(0xff, 0xff, 0xff, 200));
      p.drawText(QRect(left_limit, line_y, avail, line_h),
                 Qt::AlignHCenter | Qt::AlignVCenter, lat_debug);
    }
  }

  p.restore();
}

void NvgWindow::ctTextAnimStart(int x, int y, const QString &text, int size, const QColor &color) {
  if (!show_gear_animation) return;
  anim_x = x;
  anim_y = y;
  anim_text = text;
  anim_size = size;
  anim_color = color;
  anim_time = 130;
}

void NvgWindow::drawTextAnim(QPainter &p) {
  if (anim_time <= 0) return;
  anim_time -= 10;
  if (anim_time <= 0) { anim_time = 0; return; }

  int t = std::min(anim_time, 100);          // 보간 계수 100(시작) → 0(도착)
  const int a_max = 100;
  int x    = (width() / 2 * t + anim_x * (a_max - t)) / a_max;
  int y    = ((height() - 400) * t + anim_y * (a_max - t)) / a_max;
  int size = (350 * t + anim_size * (a_max - t)) / a_max;
  if (size < 1) return;

  p.save();
  p.setRenderHint(QPainter::Antialiasing);
  p.setRenderHint(QPainter::TextAntialiasing);
  // 등장 직후에는 두꺼운 검정 외곽선을 덧대 강조
  ctText(p, x, y, anim_text, size, anim_color, true, anim_time >= 100);
  p.restore();
}

// 리드 박스 폭 제한 (앞차가 가까울 때 화면을 다 덮지 않도록)
#define LEAD_BOX_MIN_W 180.0f
#define LEAD_BOX_MAX_W 320.0f

void NvgWindow::drawCarrotLead(QPainter &p) {
  p.save();
  p.setRenderHint(QPainter::Antialiasing);

  UIState *s = uiState();
  const UIScene &scene = s->scene;
  const bool is_metric = scene.is_metric;
  const float m_to_disp = is_metric ? 1.0f : (float)METER_TO_FOOT;

  // ---- 리드2 프레임 (있을 때만, 뒤쪽에 먼저) ----
  if (scene.lead_status[1]) {
    float xl = scene.lead_left[1].x();
    float xr = scene.lead_right[1].x();
    float ly = scene.lead_left[1].y();
    float w2 = std::clamp(xr - xl, 80.0f, LEAD_BOX_MAX_W * 0.8f);
    QRect box2((int)(xl - 10), (int)(ly - w2 * 0.8f), (int)(w2 + 20), (int)(w2 * 0.8f));
    p.setPen(QPen(QColor(218, 111, 37, 255), 5));      // 황토색
    p.setBrush(QColor(0, 0, 0, 45));
    p.drawRoundedRect(box2, 15, 15);
  }

  // ---- 리드1 프레임 ----
  if (scene.lead_status[0]) {
    float xl = scene.lead_left[0].x();
    float xr = scene.lead_right[0].x();
    float ly = scene.lead_left[0].y();
    // 앞차가 가까우면 투영 폭이 폭발하므로 상한을 두고, 프레임 간 EMA 로 눌러준다.
    float w_raw = std::clamp(xr - xl, LEAD_BOX_MIN_W, LEAD_BOX_MAX_W);
    float cx_raw = std::clamp((xl + xr) / 2.0f, 200.0f, (float)width() - 200.0f);
    float cy_raw = std::clamp(ly, 150.0f, (float)height() - 120.0f);

    const float a = 0.85f;   // 클수록 부드럽고 느리다
    if (lead_box_w <= 0.0f) { lead_box_w = w_raw; lead_box_x = cx_raw; lead_box_y = cy_raw; }
    lead_box_w = lead_box_w * a + w_raw * (1.0f - a);
    lead_box_x = lead_box_x * a + cx_raw * (1.0f - a);
    lead_box_y = lead_box_y * a + cy_raw * (1.0f - a);

    float w1 = lead_box_w;
    float cx = lead_box_x;
    ly = lead_box_y;

    // 레이더가 잡은 리드면 주황, 비전만이면 파랑
    QColor stroke = scene.lead_radar[0] ? QColor(255, 175, 3, 255) : QColor(0, 0, 255, 255);
    QRect box((int)(cx - w1 / 2 - 10), (int)(ly - w1 * 0.8f), (int)(w1 + 20), (int)(w1 * 0.8f));
    p.setPen(QPen(stroke, 6));
    p.setBrush(QColor(0, 0, 0, 55));
    p.drawRoundedRect(box, 15, 15);

    // ---- 거리 두 개 : 좌 레이더 / 우 비전 ----
    int dy = (int)ly + 60;
    QString str;

    if (scene.lead_radar_dist > 0.0f) {
      str.sprintf("%.1f", scene.lead_radar_dist * m_to_disp);
      QRect tag((int)(cx - 80 - 45), dy - 35, 90, 42);
      ctRect(p, tag, QColor(255, 175, 3, 255), 15);
      ctTextIn(p, tag, str, 40, QColor(255, 255, 255, 255));
    }
    if (scene.lead_vision_dist > 0.0f) {
      str.sprintf("%.1f", scene.lead_vision_dist * m_to_disp);
      QRect tag((int)(cx + 80 - 45), dy - 35, 90, 42);
      ctRect(p, tag, QColor(0, 0, 255, 255), 15);
      ctTextIn(p, tag, str, 40, QColor(255, 255, 255, 255));
    }
  }

  if (!scene.lead_status[0]) lead_box_w = 0.0f;   // 리드 사라지면 EMA 초기화

  p.restore();
}

void NvgWindow::drawCarrotHud(QPainter &p) {
  p.save();
  p.setRenderHint(QPainter::Antialiasing);
  p.setRenderHint(QPainter::TextAntialiasing);
  p.setOpacity(1.0);

  UIState *s = uiState();
  const SubMaster &sm = *(s->sm);
  const auto car_state    = sm["carState"].getCarState();
  const auto scc_smoother = sm["carControl"].getCarControl().getSccSmoother();
  const auto road_limit   = sm["roadLimitSpeed"].getRoadLimitSpeed();
  const auto gps          = sm["gpsLocationExternal"].getGpsLocationExternal();

  const bool  is_metric   = s->scene.is_metric;
  const float ms_to_disp  = is_metric ? MS_TO_KPH : MS_TO_MPH;
  const float kph_to_disp = is_metric ? 1.0f : KM_TO_MILE;

  blink_timer = (blink_timer + 1) % 16;

  // 파라미터는 매 프레임 읽지 않고 1초에 한번만
  if (++carrot_param_timer >= UI_FREQ) {
    carrot_param_timer = 0;
    Params params;
    int m = std::atoi(params.get("MyDrivingMode").c_str());
    my_driving_mode = (m >= 1 && m <= 4) ? m : 3;
    show_device_state = std::atoi(params.get("ShowDeviceState").c_str());
    carrot_atc_mode = std::atoi(params.get("CarrotAutoTurnControl").c_str());
    std::string sdt = params.get("ShowDateTime");
    show_datetime = sdt.empty() ? 1 : std::atoi(sdt.c_str());   // 0:끔 1:시간+날짜 2:시간만 3:날짜만
    std::string sga = params.get("ShowGearAnimation");
    show_gear_animation = sga.empty() ? 1 : std::atoi(sga.c_str());
    show_bsd_always = std::atoi(params.get("ShowBlindSpotAlways").c_str());
  }

  // ---- 기준 좌표 (carrot.cc 와 동일) ----
  const int x  = 140;
  const int y  = height() - 500;
  const int bx = x;
  const int by = y + 270;

  // ---- 단속 카메라 감지 ----
  const int cam_limit = road_limit.getCamLimitSpeed();
  const int cam_dist  = road_limit.getCamLimitSpeedLeftDist();
  const int sec_limit = road_limit.getSectionLimitSpeed();
  const int sec_dist  = road_limit.getSectionLeftDist();
  const bool cam_detected = (cam_limit > 0 && cam_dist > 0) || (sec_limit > 0 && sec_dist > 0);

  // ---- 패널 배경 ----
  QColor bg_color = (cam_detected && blink_timer > 8) ? CT_RED_A(180) : CT_BLACK_A(90);
  if (show_device_state > 0) {
    ctRect(p, QRect(bx - 120, by - 270, 475, 495), bg_color, 30, 2, CT_WHITE);
  } else {
    ctRect(p, QRect(bx - 120, by - 130, 475, 355), bg_color, 30, 2, CT_WHITE);
  }

  // ---- 현재 속도 ----
  float v_ego_disp = std::max(0.0f, (float)car_state.getVEgoCluster()) * ms_to_disp;
  ctText(p, bx, by + 50, QString::number((int)(v_ego_disp + 0.5f)), 120, CT_WHITE, true, true);
  if (!ic_speed_bg.isNull()) {
    p.setOpacity(1.0);
    p.drawPixmap(QRect(bx - 100, by - 60, 350, 150), ic_speed_bg);
  }

  // ---- 크루즈 설정 속도 ----
  float cruise_max = scc_smoother.getCruiseMaxSpeed();
  bool  is_cruise_set = (cruise_max > 0 && cruise_max < 255);
  QString cruise_str = is_cruise_set
                     ? QString::number((int)(cruise_max * kph_to_disp + 0.5f))
                     : QString("--");
  ctText(p, bx + 170, by + 15, cruise_str, 60, CT_GREEN, true, true);

  // ---- 적용 속도(감속 목표) + 감속 사유 : carrot 의 apply_speed / apply_source ----
  //      sccSmoother 계열(cam/sec/road/eco) 과 VisionTurnController(vturn) 중
  //      더 낮은 목표속도를 표시한다.
  float show_speed = 0.0f;      // kph
  QString src = "";

  float apply_max = scc_smoother.getApplyMaxSpeed();
  if (is_cruise_set && apply_max > 0 && std::abs(apply_max - cruise_max) > 0.5f) {
    show_speed = apply_max;
    if (cam_limit > 0 && cam_dist > 0)       src = "cam";
    else if (sec_limit > 0 && sec_dist > 0)  src = "sec";
    else if (road_limit.getActive() > 0 && road_limit.getRoadLimitSpeed() > 0 &&
             apply_max <= road_limit.getRoadLimitSpeed() + 1)  src = "road";
    else                                     src = "eco";
  }

  // VisionTurnController 커브 감속
  {
    const auto long_plan = sm["longitudinalPlan"].getLongitudinalPlan();
    auto vtc = long_plan.getVisionTurnControllerState();
    bool vtc_active = (vtc == cereal::LongitudinalPlan::VisionTurnControllerState::ENTERING ||
                       vtc == cereal::LongitudinalPlan::VisionTurnControllerState::TURNING);
    if (vtc_active && s->engaged()) {
      float v_turn = long_plan.getVisionTurnSpeed() * 3.6f;   // m/s -> kph
      if (v_turn > 0 && (show_speed <= 0.0f || v_turn < show_speed)) {
        show_speed = v_turn;
        src = "vturn";
      }
    }
  }

  if (show_speed > 0.0f && !src.isEmpty()) {
    ctText(p, bx + 250, by - 50,  QString::number((int)(show_speed * kph_to_disp + 0.5f)),
           50, CT_OCHRE, true, true);
    ctText(p, bx + 250, by - 100, src, 30, CT_OCHRE, true, true);
  }

  // ---- 주행모드 (NORM / ECO / SAFE / FAST) ----
  QString mode_str = "NORM";
  QColor  mode_color = CT_GREY_A(210);
  switch (my_driving_mode) {
    case 1: mode_str = "ECO";  mode_color = CT_GREEN_A(210);  break;
    case 2: mode_str = "SAFE"; mode_color = CT_ORANGE_A(210); break;
    case 3: mode_str = "NORM"; mode_color = CT_GREY_A(210);   break;
    case 4: mode_str = "FAST"; mode_color = CT_RED_A(210);    break;
  }
  {
    int dx = bx - 50;
    int dy = by + 175;
    QRect mode_box(dx - 55, dy - 38, 110, 48);
    ctRect(p, mode_box, mode_color, 15, 2);
    ctTextIn(p, mode_box, mode_str, 32, CT_WHITE);
    if (gps.getFlags() > 0 && gps.getAccuracy() > 0.01f && gps.getAccuracy() < 20.f) {
      ctText(p, dx, dy - 45, "GPS", 30, CT_GREEN, true);
    }
  }

  // ---- 차간거리(GAP) 막대 ----
  int gap = car_state.getCruiseGap();
  {
    int   dx  = bx + 270;
    int   dy  = by + 185;
    float ddy = 80 / 4.0f;
    for (int i = 0; i < gap && i < 4; i++) {
      ctRect(p, QRect(dx, (int)(dy - ddy * (i + 1) + 2), 70, (int)ddy - 2),
             CT_GREEN_A(210), 4, 3, CT_WHITE);
    }
  }

  // ---- 기어 (carrot.cc 와 동일: D 에서는 변속단수 표시) ----
  {
    QString gear_str = "M";
    switch (car_state.getGearShifter()) {
      case cereal::CarState::GearShifter::UNKNOWN: gear_str = "U"; break;
      case cereal::CarState::GearShifter::PARK:    gear_str = "P"; break;
      case cereal::CarState::GearShifter::DRIVE:
        if (car_state.getGearStep() > 0) gear_str = QString::number(car_state.getGearStep());
        else                             gear_str = "D";
        break;
      case cereal::CarState::GearShifter::NEUTRAL: gear_str = "N"; break;
      case cereal::CarState::GearShifter::REVERSE: gear_str = "R"; break;
      case cereal::CarState::GearShifter::SPORT:   gear_str = "S"; break;
      case cereal::CarState::GearShifter::LOW:     gear_str = "L"; break;
      case cereal::CarState::GearShifter::BRAKE:   gear_str = "B"; break;
      case cereal::CarState::GearShifter::ECO:     gear_str = "E"; break;
      default: gear_str = "M"; break;
    }
    int dx = bx + 305;
    int dy = by + 60;
    QRect gear_box(dx - 35, dy - 70, 70, 80);
    ctRect(p, gear_box, CT_GREEN_A(210), 15, 3, CT_WHITE);
    ctTextIn(p, gear_box, gear_str, 70, CT_WHITE);

    // 기어가 바뀌면 팝업 애니메이션 시작
    if (!gear_str_last.isEmpty() && gear_str_last != gear_str) {
      ctTextAnimStart(gear_box.center().x(), gear_box.bottom(), gear_str, 70, CT_WHITE);
    }
    gear_str_last = gear_str;
  }

  // ---- MAP / NDA / HDA (carrot 의 APN/APM 자리. roadLimitSpeed.active) ----
  //      목적지 안내 중 : MAP
  //      active < 2    : 일반도로(NDA)
  //      active >= 2   : 고속도로/자동차전용도로(HDA)
  {
    int active = road_limit.getActive();
    int dx = bx + 200;
    int dy = by + 175;
    QRect nda_box(dx - 55, dy - 38, 110, 48);

    const qint64 wall_now = QDateTime::currentMSecsSinceEpoch();
    bool navi_active = carrot_navi_route.size() >= 2 &&
                       (carrot_navi_remain_distance > 0 || carrot_navi_remain_time > 0) &&
                       carrot_navi_updated_at != 0 &&
                       (wall_now - static_cast<qint64>(carrot_navi_updated_at)) <= 35000;

    if (navi_active) {
      ctRect(p, nda_box, CT_BLUE_A(210), 15, 2);
      ctTextIn(p, nda_box, "MAP", 40, CT_WHITE);
    } else if (active >= 2) {
      ctRect(p, nda_box, CT_GREEN, 15, 2);
      ctTextIn(p, nda_box, "HDA", 40, CT_WHITE);
    } else {
      ctRect(p, nda_box, CT_BLUE_A(210), 15, 2);
      ctTextIn(p, nda_box, "NDA", 40, CT_WHITE);
    }
  }

  // ---- LIMIT / CAM ----
  {
    int dx = bx + 75;
    int dy = by + 175;
    int disp_speed = 0;
    QColor limit_color;
    QColor limit_text_color = CT_WHITE;

    if (cam_detected) {
      int limit = (cam_dist > 0) ? cam_limit : sec_limit;
      disp_speed = (int)(limit * kph_to_disp + 0.5f);
      limit_color = (blink_timer <= 8) ? CT_RED_A(210) : CT_YELLOW_A(210);
      ctText(p, dx, dy - 45, "CAM", 30, CT_WHITE, true);
    } else {
      int limit = road_limit.getRoadLimitSpeed();
      if (limit <= 0 && carrot_navi_speed_limit > 0 && carrot_navi_updated_at != 0 &&
          QDateTime::currentMSecsSinceEpoch() - (qint64)carrot_navi_updated_at <= 35000) {
        limit = carrot_navi_speed_limit;   // 내비 중 carrot 제한속도 폴백
      }
      disp_speed = (int)(limit * kph_to_disp + 0.5f);
      bool over = (car_state.getVEgoCluster() * 3.6f > limit + 2) && limit > 0;
      limit_color = over ? CT_RED_A(210) : CT_WHITE_A(210);
      // 흰 박스 위에서는 흰 글씨가 안보이므로 검정으로
      if (!over) limit_text_color = CT_BLACK_A(230);
      ctText(p, dx, dy - 45, "LIMIT", 30, CT_WHITE, true);
    }
    QRect limit_box(dx - 55, dy - 38, 110, 48);
    ctRect(p, limit_box, limit_color, 15, 2);
    ctTextIn(p, limit_box, QString::number(disp_speed), 40, limit_text_color);
  }

  // ---- 디바이스 상태 (ShowDeviceState = 1 일 때만) ----
  if (show_device_state > 0) {
    const auto deviceState = sm["deviceState"].getDeviceState();
    float cpuTemp = 0.f;
    const auto cpuTempC = deviceState.getCpuTempC();
    if (std::size(cpuTempC) > 0) {
      for (int i = 0; i < (int)std::size(cpuTempC); i++) cpuTemp += cpuTempC[i];
      cpuTemp /= (float)std::size(cpuTempC);
    }
    float ambientTemp = deviceState.getAmbientTempC();

    int dx = bx - 35;
    int dy = by - 200;
    QColor box = CT_GREEN_A(190);
    QString str;

    QRect ds_box(dx - 65, dy - 38, 130, 90);
    ctRect(p, ds_box, (cpuTemp > 80 && blink_timer <= 8) ? CT_RED_A(255) : box, 15, 2);
    ctTextIn(p, QRect(ds_box.x(), ds_box.y(), ds_box.width(), 34), "CPU", 25, CT_WHITE);
    str.sprintf("%.0f\u00B0C", cpuTemp);
    ctTextIn(p, QRect(ds_box.x(), ds_box.y() + 34, ds_box.width(), 56), str, 40, CT_WHITE);

    dx += 150;
    ds_box.moveLeft(dx - 65);
    const qint64 wall_now = QDateTime::currentMSecsSinceEpoch();
    const qint64 guidance_age = wall_now - static_cast<qint64>(carrot_navi_guidance_updated_at);
    const bool atc_enabled = carrot_atc_mode >= 1 && carrot_atc_mode <= 3;
    const bool atc_fresh = carrot_navi_guidance_updated_at != 0 &&
                           guidance_age >= -5000 && guidance_age <= 3000;
    int atc_direction = 0;
    const CarrotAtcKind atc_kind = carrotAtcKind(carrot_navi_turn_type,
                                                 carrot_navi_instruction, &atc_direction);
    const float v_ego = car_state.getVEgo();
    const float trigger_distance = std::max(35.0f, std::min(70.0f, v_ego * 3.0f));
    const bool opposite_torque = car_state.getSteeringPressed() &&
      ((atc_direction < 0 && car_state.getSteeringTorque() < 0) ||
       (atc_direction > 0 && car_state.getSteeringTorque() > 0));
    const bool conflicting_blinker =
      (atc_direction < 0 && car_state.getRightBlinker()) ||
      (atc_direction > 0 && car_state.getLeftBlinker());
    const bool steering_active = atc_fresh && (carrot_atc_mode == 1 || carrot_atc_mode == 2) &&
      sm["carControl"].getCarControl().getLatActive() && !car_state.getBrakePressed() &&
      !opposite_torque && !conflicting_blinker &&
      (atc_kind == CarrotAtcKind::TURN || atc_kind == CarrotAtcKind::UTURN) &&
      carrot_navi_distance >= 3 && carrot_navi_distance <= trigger_distance &&
      v_ego <= 60.0f / 3.6f;
    const bool speed_active = atc_fresh && (carrot_atc_mode == 2 || carrot_atc_mode == 3) &&
      !car_state.getBrakePressed() &&
      (atc_kind == CarrotAtcKind::TURN || atc_kind == CarrotAtcKind::UTURN ||
       atc_kind == CarrotAtcKind::ROTARY) &&
      carrot_navi_distance >= 0 && carrot_navi_distance <= 350;

    QColor atc_color = CT_GREY_A(210);  // off / waiting
    if (atc_enabled && !atc_fresh) atc_color = CT_RED_A(230);       // data lost
    else if (steering_active)     atc_color = CT_BLUE_A(230);      // steering
    else if (speed_active)        atc_color = CT_ORANGE_A(230);    // slowing

    ctRect(p, ds_box, atc_color, 15, 2);
    ctTextIn(p, QRect(ds_box.x(), ds_box.y(), ds_box.width(), 34), "ATC", 25, CT_WHITE);
    const QString atc_distance = atc_fresh && carrot_navi_distance >= 0
      ? QString("%1m").arg(carrot_navi_distance) : QString("--");
    ctTextIn(p, QRect(ds_box.x(), ds_box.y() + 34, ds_box.width(), 56), atc_distance, 36, CT_WHITE);

    dx += 150;
    ds_box.moveLeft(dx - 65);
    ctRect(p, ds_box, (ambientTemp > 50 && blink_timer <= 8) ? CT_RED_A(255) : box, 15, 2);
    ctTextIn(p, QRect(ds_box.x(), ds_box.y(), ds_box.width(), 34), "AMB", 25, CT_WHITE);
    str.sprintf("%.0f\u00B0C", ambientTemp);
    ctTextIn(p, QRect(ds_box.x(), ds_box.y() + 34, ds_box.width(), 56), str, 40, CT_WHITE);
  }

  p.restore();
}

void NvgWindow::drawMaxSpeed(QPainter &p) {
  p.save();
  UIState *s = uiState();
  const SubMaster &sm = *(s->sm);
  const auto scc_smoother = sm["carControl"].getCarControl().getSccSmoother();
  bool is_metric = s->scene.is_metric;
  bool long_control = scc_smoother.getLongControl();

  // kph
  float applyMaxSpeed = scc_smoother.getApplyMaxSpeed();
  float cruiseMaxSpeed = scc_smoother.getCruiseMaxSpeed();
  bool is_cruise_set = (cruiseMaxSpeed > 0 && cruiseMaxSpeed < 255);

  QRect rc(30, 30, 184, 202);
  p.setPen(QPen(QColor(0xff, 0xff, 0xff, 100), 10));
  p.setBrush(QColor(0, 0, 0, 100));
  p.drawRoundedRect(rc, 20, 20);
  p.setPen(Qt::NoPen);

  if (is_cruise_set) {
    char str[256];
    if (is_metric)
        snprintf(str, sizeof(str), "%d", (int)(applyMaxSpeed + 0.5));
    else
        snprintf(str, sizeof(str), "%d", (int)(applyMaxSpeed*KM_TO_MILE + 0.5));

    configFont(p, "Open Sans", 45, "Bold");
    drawText(p, rc.center().x(), 100, str, 255);

    if (is_metric)
        snprintf(str, sizeof(str), "%d", (int)(cruiseMaxSpeed + 0.5));
    else
        snprintf(str, sizeof(str), "%d", (int)(cruiseMaxSpeed*KM_TO_MILE + 0.5));

    configFont(p, "Open Sans", 76, "Bold");
    drawText(p, rc.center().x(), 195, str, 255);
  } else {
    if(long_control) {
      configFont(p, "Open Sans", 48, "sans-semibold");
      drawText(p, rc.center().x(), 100, "OP", 100);
    }
    else {
      configFont(p, "Open Sans", 48, "sans-semibold");
      drawText(p, rc.center().x(), 100, "MAX", 100);
    }

    configFont(p, "Open Sans", 76, "sans-semibold");
    drawText(p, rc.center().x(), 195, "N/A", 100);
  }
  p.restore();
}

void NvgWindow::drawSpeed(QPainter &p) {
  p.save();
  UIState *s = uiState();
  const SubMaster &sm = *(s->sm);
  float cur_speed = std::max(0.0, sm["carState"].getCarState().getVEgoCluster() * (s->scene.is_metric ? MS_TO_KPH : MS_TO_MPH));
  auto car_state = sm["carState"].getCarState();
  float accel = car_state.getAEgo();

  QColor color = QColor(255, 255, 255, 230);

  if(accel > 0) {
    int a = (int)(255.f - (180.f * (accel/2.f)));
    a = std::min(a, 255);
    a = std::max(a, 80);
    color = QColor(a, a, 255, 230);
  }
  else {
    int a = (int)(255.f - (255.f * (-accel/3.f)));
    a = std::min(a, 255);
    a = std::max(a, 60);
    color = QColor(255, a, a, 230);
  }

  QString speed;
  speed.sprintf("%.0f", cur_speed);
  configFont(p, "Open Sans", 176, "Bold");
  drawTextWithColor(p, rect().center().x(), 230, speed, color);

  configFont(p, "Open Sans", 66, "Regular");
  drawText(p, rect().center().x(), 310, s->scene.is_metric ? "km/h" : "mph", 200);

  p.restore();	
}

void NvgWindow::drawSpeedLimit(QPainter &p) {
  p.save();
	
  const SubMaster &sm = *(uiState()->sm);
  auto roadLimitSpeed = sm["roadLimitSpeed"].getRoadLimitSpeed();

  int camLimitSpeed = roadLimitSpeed.getCamLimitSpeed();
  int camLimitSpeedLeftDist = roadLimitSpeed.getCamLimitSpeedLeftDist();

  int sectionLimitSpeed = roadLimitSpeed.getSectionLimitSpeed();
  int sectionLeftDist = roadLimitSpeed.getSectionLeftDist();

  int limit_speed = 0;
  int left_dist = 0;

  if(camLimitSpeed > 0 && camLimitSpeedLeftDist > 0) {
    limit_speed = camLimitSpeed;
    left_dist = camLimitSpeedLeftDist;
  }
  else if(sectionLimitSpeed > 0 && sectionLeftDist > 0) {
    limit_speed = sectionLimitSpeed;
    left_dist = sectionLeftDist;
  }

  // NDA/HDA 아이콘 --- carrot hud panel 안의 NDA/HDA 텍스트로 대체되어 제거함

  if(limit_speed > 10 && limit_speed < 130)
  {
    int radius_ = 192;

    int x = 30;
    int y = 270;

    p.setPen(Qt::NoPen);
    p.setBrush(QBrush(QColor(255, 0, 0, 255)));
    QRect rect = QRect(x, y, radius_, radius_);
    p.drawEllipse(rect);

    p.setBrush(QBrush(QColor(255, 255, 255, 255)));

    const int tickness = 14;
    rect.adjust(tickness, tickness, -tickness, -tickness);
    p.drawEllipse(rect);

    QString str_limit_speed, str_left_dist;
    str_limit_speed.sprintf("%d", limit_speed);

    if(left_dist >= 1000)
      str_left_dist.sprintf("%.1fkm", left_dist / 1000.f);
    else if(left_dist > 0)
      str_left_dist.sprintf("%dm", left_dist);

    configFont(p, "Open Sans", 80, "Bold");
    p.setPen(QColor(0, 0, 0, 230));
    p.drawText(rect, Qt::AlignCenter, str_limit_speed);

    if(str_left_dist.length() > 0) {
      configFont(p, "Open Sans", 60, "Bold");
      rect.translate(0, radius_/2 + 45);
      rect.adjust(-30, 0, 30, 0);
      p.setPen(QColor(255, 255, 255, 230));
      p.drawText(rect, Qt::AlignCenter, str_left_dist);
    }
  }
  else {
    auto controls_state = sm["controlsState"].getControlsState();
    int sccStockCamAct = (int)controls_state.getSccStockCamAct();
    int sccStockCamStatus = (int)controls_state.getSccStockCamStatus();

    if(sccStockCamAct == 2 && sccStockCamStatus == 2) {
      int radius_ = 192;

      int x = 30;
      int y = 270;

      p.setPen(Qt::NoPen);

      p.setBrush(QBrush(QColor(255, 0, 0, 255)));
      QRect rect = QRect(x, y, radius_, radius_);
      p.drawEllipse(rect);

      p.setBrush(QBrush(QColor(255, 255, 255, 255)));

      const int tickness = 14;
      rect.adjust(tickness, tickness, -tickness, -tickness);
      p.drawEllipse(rect);

      configFont(p, "Open Sans", 70, "Bold");
      p.setPen(QColor(0, 0, 0, 230));
      p.drawText(rect, Qt::AlignCenter, "CAM");
    }
  }

  p.restore();
}

void NvgWindow::drawSteer(QPainter &p) {
  p.save();

  int x = 30;
  int y = 540;

  const SubMaster &sm = *(uiState()->sm);
  auto car_state = sm["carState"].getCarState();
  auto car_control = sm["carControl"].getCarControl();

  float steer_angle = car_state.getSteeringAngleDeg();
  float desire_angle = car_control.getActuators().getSteeringAngleDeg();

  configFont(p, "Open Sans", 50, "Bold");

  QString str;
  int width = 192;

  str.sprintf("%.0f°", steer_angle);
  QRect rect = QRect(x, y, width, width);

  p.setPen(QColor(255, 255, 255, 200));
  p.drawText(rect, Qt::AlignCenter, str);

  str.sprintf("%.0f°", desire_angle);
  rect.setRect(x, y + 80, width, width);

  p.setPen(QColor(155, 255, 155, 200));
  p.drawText(rect, Qt::AlignCenter, str);

  p.restore();
}

template <class T>
float interp(float x, std::initializer_list<T> x_list, std::initializer_list<T> y_list, bool extrapolate)
{
  std::vector<T> xData(x_list);
  std::vector<T> yData(y_list);
  int size = xData.size();

  int i = 0;
  if(x >= xData[size - 2]) {
    i = size - 2;
  }
  else {
    while ( x > xData[i+1] ) i++;
  }
  T xL = xData[i], yL = yData[i], xR = xData[i+1], yR = yData[i+1];
  if (!extrapolate) {
    if ( x < xL ) yR = yL;
    if ( x > xR ) yL = yR;
  }

  T dydx = ( yR - yL ) / ( xR - xL );
  return yL + dydx * ( x - xL );
}

void NvgWindow::drawThermal(QPainter &p) {
  p.save();

  const SubMaster &sm = *(uiState()->sm);
  auto deviceState = sm["deviceState"].getDeviceState();

  const auto cpuTempC = deviceState.getCpuTempC();
  //const auto gpuTempC = deviceState.getGpuTempC();
  float ambientTemp = deviceState.getAmbientTempC();

  float cpuTemp = 0.f;
  //float gpuTemp = 0.f;

  if(std::size(cpuTempC) > 0) {
    for(int i = 0; i < std::size(cpuTempC); i++) {
      cpuTemp += cpuTempC[i];
    }
    cpuTemp = cpuTemp / (float)std::size(cpuTempC);
  }

  int w = 192;
  int x = width() - (30 + w);
  int y = 450;

  QString str;
  QRect rect;

  configFont(p, "Open Sans", 50, "Bold");
  str.sprintf("%.0f°C", cpuTemp);
  rect = QRect(x, y, w, w);

  int r = interp<float>(cpuTemp, {50.f, 90.f}, {200.f, 255.f}, false);
  int g = interp<float>(cpuTemp, {50.f, 90.f}, {255.f, 200.f}, false);
  p.setPen(QColor(r, g, 200, 200));
  p.drawText(rect, Qt::AlignCenter, str);

  y += 55;
  configFont(p, "Open Sans", 25, "Bold");
  rect = QRect(x, y, w, w);
  p.setPen(QColor(255, 255, 255, 200));
  p.drawText(rect, Qt::AlignCenter, "CPU");

  y += 80;
  configFont(p, "Open Sans", 50, "Bold");
  str.sprintf("%.0f°C", ambientTemp);
  rect = QRect(x, y, w, w);
  r = interp<float>(ambientTemp, {35.f, 60.f}, {200.f, 255.f}, false);
  g = interp<float>(ambientTemp, {35.f, 60.f}, {255.f, 200.f}, false);
  p.setPen(QColor(r, g, 200, 200));
  p.drawText(rect, Qt::AlignCenter, str);

  y += 55;
  configFont(p, "Open Sans", 25, "Bold");
  rect = QRect(x, y, w, w);
  p.setPen(QColor(255, 255, 255, 200));
  p.drawText(rect, Qt::AlignCenter, "AMBIENT");

  p.restore();
}

void NvgWindow::drawTurnSignals(QPainter &p) {
  p.save();
	
  static int blink_index = 0;
  static int blink_wait = 0;
  static double prev_ts = 0.0;

  if(blink_wait > 0) {
    blink_wait--;
    blink_index = 0;
  }
  else {
    const SubMaster &sm = *(uiState()->sm);
    auto car_state = sm["carState"].getCarState();
    bool left_on = car_state.getLeftBlinker();
    bool right_on = car_state.getRightBlinker();

    const float img_alpha = 0.8f;
    const int fb_w = width() / 2 - 200;
    const int center_x = width() / 2;
    const int w = fb_w / 25;
    const int h = 160;
    const int gap = fb_w / 25;
    const int margin = (int)(fb_w / 3.8f);
    const int base_y = (height() - h) / 2;
    const int draw_count = 8;

    int x = center_x;
    int y = base_y;

    if(left_on) {
      for(int i = 0; i < draw_count; i++) {
        float alpha = img_alpha;
        int d = std::abs(blink_index - i);
        if(d > 0)
          alpha /= d*2;

        p.setOpacity(alpha);
        float factor = (float)draw_count / (i + draw_count);
        p.drawPixmap(x - w - margin, y + (h-h*factor)/2, w*factor, h*factor, ic_turn_signal_l);
        x -= gap + w;
      }
    }

    x = center_x;
    if(right_on) {
      for(int i = 0; i < draw_count; i++) {
        float alpha = img_alpha;
        int d = std::abs(blink_index - i);
        if(d > 0)
          alpha /= d*2;

        float factor = (float)draw_count / (i + draw_count);
        p.setOpacity(alpha);
        p.drawPixmap(x + margin, y + (h-h*factor)/2, w*factor, h*factor, ic_turn_signal_r);
        x += gap + w;
      }
    }

    if(left_on || right_on) {

      double now = millis_since_boot();
      if(now - prev_ts > 900/UI_FREQ) {
        prev_ts = now;
        blink_index++;
      }

      if(blink_index >= draw_count) {
        blink_index = draw_count - 1;
        blink_wait = UI_FREQ/4;
      }
    }
    else {
      blink_index = 0;
    }
  }

  p.restore();
}

void NvgWindow::drawGpsStatus(QPainter &p) {
  const SubMaster &sm = *(uiState()->sm);
  auto gps = sm["gpsLocationExternal"].getGpsLocationExternal();
  float accuracy = gps.getAccuracy();
  if(accuracy < 0.01f || accuracy > 20.f)
    return;

  int w = 120;
  int h = 100;
  int x = width() - w - 30;
  int y = 30;

  p.save();

  p.setOpacity(0.8);
  p.drawPixmap(x, y, w, h, ic_satellite);

  configFont(p, "Open Sans", 40, "Bold");
  p.setPen(QColor(255, 255, 255, 200));
  p.setRenderHint(QPainter::TextAntialiasing);

  QRect rect = QRect(x, y + h + 10, w, 40);
  rect.adjust(-30, 0, 30, 0);

  QString str;
  str.sprintf("%.1fm", accuracy);
  p.drawText(rect, Qt::AlignHCenter, str);
	
  p.restore();
}

void NvgWindow::drawDebugText(QPainter &p) {
  p.save();
  const SubMaster &sm = *(uiState()->sm);
  QString str, temp;

  int y = 80;
  const int height = 60;

  const int text_x = width()/2 + 250;

  auto controls_state = sm["controlsState"].getControlsState();
  auto car_control = sm["carControl"].getCarControl();
  auto car_state = sm["carState"].getCarState();

  float applyAccel = controls_state.getApplyAccel();

  float aReqValue = controls_state.getAReqValue();
  float aReqValueMin = controls_state.getAReqValueMin();
  float aReqValueMax = controls_state.getAReqValueMax();

  float vEgo = car_state.getVEgo();
  float vEgoRaw = car_state.getVEgoRaw();
  int longControlState = (int)controls_state.getLongControlState();
  float vPid = controls_state.getVPid();
  float upAccelCmd = controls_state.getUpAccelCmd();
  float uiAccelCmd = controls_state.getUiAccelCmd();
  float ufAccelCmd = controls_state.getUfAccelCmd();
  float accel = car_control.getActuators().getAccel();

  const char* long_state[] = {"off", "pid", "stopping", "starting"};

  configFont(p, "Open Sans", 35, "Regular");
  p.setPen(QColor(255, 255, 255, 200));
  p.setRenderHint(QPainter::TextAntialiasing);

  str.sprintf("State: %s\n", long_state[longControlState]);
  p.drawText(text_x, y, str);

  y += height;
  str.sprintf("vEgo: %.2f/%.2f\n", vEgo*3.6f, vEgoRaw*3.6f);
  p.drawText(text_x, y, str);

  y += height;
  str.sprintf("vPid: %.2f/%.2f\n", vPid, vPid*3.6f);
  p.drawText(text_x, y, str);

  y += height;
  str.sprintf("P: %.3f\n", upAccelCmd);
  p.drawText(text_x, y, str);

  y += height;
  str.sprintf("I: %.3f\n", uiAccelCmd);
  p.drawText(text_x, y, str);

  y += height;
  str.sprintf("F: %.3f\n", ufAccelCmd);
  p.drawText(text_x, y, str);

  y += height;
  str.sprintf("Accel: %.3f\n", accel);
  p.drawText(text_x, y, str);

  y += height;
  str.sprintf("Apply: %.3f, Stock: %.3f\n", applyAccel, aReqValue);
  p.drawText(text_x, y, str);

  y += height;
  str.sprintf("%.3f (%.3f/%.3f)\n", aReqValue, aReqValueMin, aReqValueMax);
  p.drawText(text_x, y, str);

  y += height;
  str.sprintf("aEgo: %.3f, %.3f\n", car_state.getAEgo(), car_state.getABasis());
  p.drawText(text_x, y, str);

  auto lead_radar = sm["radarState"].getRadarState().getLeadOne();
  auto lead_one = sm["modelV2"].getModelV2().getLeadsV3()[0];

  float radar_dist = lead_radar.getStatus() && lead_radar.getRadar() ? lead_radar.getDRel() : 0;
  float vision_dist = lead_one.getProb() > .5 ? (lead_one.getX()[0] - 1.5) : 0;

  y += height;
  str.sprintf("Lead: %.1f/%.1f/%.1f\n", radar_dist, vision_dist, (radar_dist - vision_dist));
  p.drawText(text_x, y, str);

  p.restore();
}

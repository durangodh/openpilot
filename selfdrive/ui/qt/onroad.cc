#include "selfdrive/ui/qt/onroad.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
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

  // ?? MyDrivingMode ???꾪솚 (醫뚰븯??紐⑤뱶 諛뺤뒪) ????????????
  //    紐⑤뱶 諛뺤뒪??NvgWindow 湲곗? x 35~145, y (h-93)~(h-45).
  //    NvgWindow 媛 bdr_s 留뚰겮 ?덉そ???덉쑝誘濡?OnroadWindow 湲곗??쇰줈??
  //    嫄곗쓽 媛숈? ?꾩튂媛 ?쒕떎. ??븯湲??쎄쾶 ?ъ쑀瑜??붾떎.
  {
    int tap_x = endPos.x();
    int tap_y = endPos.y();
    if (tap_x > 20 && tap_x < 200 &&
        tap_y > height() - 140 && tap_y < height() - 40) {
      int cur = std::atoi(Params().get("MyDrivingMode").c_str());
      if (cur < 1 || cur > 4) cur = 3;
      int next = cur % 4 + 1;   // 1????????
      Params().put("MyDrivingMode", std::to_string(next));
      return;
    }
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

  // carrot hud
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

// carrot ui_draw_bsd ?댁떇.
// 踰??대━怨??욎쑝濡?吏꾪뻾 + ?ㅻ줈 蹂듦? ?쒖꽌)?????먯뵫 ?딆뼱 ?ш컖 議곌컖?쇰줈 洹몃┛??
// ???⑹뼱由щ줈 移좏븯吏 ?딆븘 ?명?由?留먮슍泥섎읆 蹂댁씤??
void NvgWindow::drawBlindSpot(QPainter &painter, const line_vertices_data &vd, const QColor &color) {
  const int n = vd.cnt;
  if (n < 6) return;

  painter.save();
  painter.setPen(Qt::NoPen);
  painter.setBrush(color);

  // ?욎そ 吏꾪뻾 援ш컙 v[i] ??吏앹? ?섎룎?꾩삤??援ш컙 v[n-1-i] ?대떎.
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

  // ?? Blind Spot (carrot 諛⑹떇) ??????????????????????????????????
  //   媛먯??먯쓣 ?뚮쭔 ?몃? ?명?由щ? ?몄슫?? ?됱긽?쒖뿉??洹몃━吏 ?딅뒗??
  //   ShowBlindSpotAlways = 1 ?대㈃ 媛먯? ?щ?? 臾닿??섍쾶 ?먮━寃??곸떆 ?쒖떆?쒕떎.
  //   (BSD ?좏샇媛 ???ㅼ뼱?ㅻ뒗吏, 洹몃━湲곌? ???섎뒗吏 援щ텇?????좎슜)
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
    p.rotate(-angle);           // 議고뼢媛곷쭔???뚯쟾 (諛섏떆怨?
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
  //drawSteer(p);      // 議고뼢媛??쒖떆 ?쒓굅
  //drawThermal(p);    // CPU/AMBIENT ?⑤룄 ?쒖떆 ?쒓굅
  //drawTurnSignals(p);
  //drawGpsStatus(p);   // GPS ?꾩꽦 ?꾩씠肄??쒖떆 ?쒓굅
  drawCarrotInfo(p);
  drawCarrotBottom(p);

  if(s->show_debug && width() > 1200)
    drawDebugText(p);

  // ?섎떒 ?붾쾭洹??뺣낫(TS/AO/SR/SAD/BUS/SCC) ?쒖떆 ?쒓굅

  drawBottomIcons(p);

  drawTextAnim(p);   // ?앹뾽 ?좊땲硫붿씠?섏? ??긽 留???
}

void NvgWindow::updateCarrotNavi() {
  const uint64_t now = millis_since_boot();
  if (now - carrot_navi_last_read < 500) return;
  carrot_navi_last_read = now;

  QFile file("/dev/shm/carrot_navi_route.json");
  if (!file.open(QIODevice::ReadOnly)) return;
  QJsonParseError err…7035 tokens truncated…nt dx = bx - 50;
    int dy = by + 175;
    QRect mode_box(dx - 55, dy - 38, 110, 48);
    ctRect(p, mode_box, mode_color, 15, 2);
    ctTextIn(p, mode_box, mode_str, 32, CT_WHITE);
    if (gps.getFlags() > 0 && gps.getAccuracy() > 0.01f && gps.getAccuracy() < 20.f) {
      ctText(p, dx, dy - 45, "GPS", 30, CT_GREEN, true);
    }
  }

  // ---- 李④컙嫄곕━(GAP) ?レ옄 + 留됰? ----
  int gap = car_state.getCruiseGap();
  ctText(p, bx + 220, by + 77, gap > 0 ? QString::number(gap) : QString("-"), 40, CT_WHITE, true);
  {
    int   dx  = bx + 270;
    int   dy  = by + 185;
    float ddy = 80 / 4.0f;
    for (int i = 0; i < gap && i < 4; i++) {
      ctRect(p, QRect(dx, (int)(dy - ddy * (i + 1) + 2), 70, (int)ddy - 2),
             CT_GREEN_A(210), 4, 3, CT_WHITE);
    }
  }

  // ---- 湲곗뼱 (carrot.cc ? ?숈씪: D ?먯꽌??蹂?띾떒???쒖떆) ----
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

    // 湲곗뼱媛 諛붾뚮㈃ ?앹뾽 ?좊땲硫붿씠???쒖옉
    if (!gear_str_last.isEmpty() && gear_str_last != gear_str) {
      ctTextAnimStart(gear_box.center().x(), gear_box.bottom(), gear_str, 70, CT_WHITE);
    }
    gear_str_last = gear_str;
  }

  // ---- NDA / HDA (carrot ??APN/APM ?먮━. roadLimitSpeed.active) ----
  //      active == 1 : ?쇰컲?꾨줈 ?띾룄?뺣낫 ?섏떊(NDA)
  //      active >= 2 : 怨좎냽?꾨줈/?먮룞李⑥쟾?⑸룄濡?HDA)
  {
    int active = road_limit.getActive();
    int dx = bx + 200;
    int dy = by + 175;
    QRect nda_box(dx - 55, dy - 38, 110, 48);
    if (active >= 2) {
      ctRect(p, nda_box, CT_GREEN, 15, 2);
      ctTextIn(p, nda_box, "HDA", 40, CT_WHITE);
    } else if (active >= 1) {
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
      disp_speed = (int)(limit * kph_to_disp + 0.5f);
      bool over = (car_state.getVEgoCluster() * 3.6f > limit + 2) && limit > 0;
      limit_color = over ? CT_RED_A(210) : CT_WHITE_A(210);
      // ??諛뺤뒪 ?꾩뿉?쒕뒗 ??湲?④? ?덈낫?대?濡?寃?뺤쑝濡?
      if (!over) limit_text_color = CT_BLACK_A(230);
      ctText(p, dx, dy - 45, "LIMIT", 30, CT_WHITE, true);
    }
    QRect limit_box(dx - 55, dy - 38, 110, 48);
    ctRect(p, limit_box, limit_color, 15, 2);
    ctTextIn(p, limit_box, QString::number(disp_speed), 40, limit_text_color);
  }

  // ---- ?붾컮?댁뒪 ?곹깭 (ShowDeviceState = 1 ???뚮쭔) ----
  if (show_device_state > 0) {
    const auto deviceState = sm["deviceState"].getDeviceState();
    float cpuTemp = 0.f;
    const auto cpuTempC = deviceState.getCpuTempC();
    if (std::size(cpuTempC) > 0) {
      for (int i = 0; i < (int)std::size(cpuTempC); i++) cpuTemp += cpuTempC[i];
      cpuTemp /= (float)std::size(cpuTempC);
    }
    int memoryUsage = deviceState.getMemoryUsagePercent();
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
    ctRect(p, ds_box, (memoryUsage > 85 && blink_timer <= 8) ? CT_RED_A(255) : box, 15, 2);
    ctTextIn(p, QRect(ds_box.x(), ds_box.y(), ds_box.width(), 34), "MEM", 25, CT_WHITE);
    str.sprintf("%d%%", memoryUsage);
    ctTextIn(p, QRect(ds_box.x(), ds_box.y() + 34, ds_box.width(), 56), str, 40, CT_WHITE);

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

  // NDA/HDA ?꾩씠肄?--- carrot hud panel ?덉쓽 NDA/HDA ?띿뒪?몃줈 ?泥대릺???쒓굅??

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

  str.sprintf("%.0f째", steer_angle);
  QRect rect = QRect(x, y, width, width);

  p.setPen(QColor(255, 255, 255, 200));
  p.drawText(rect, Qt::AlignCenter, str);

  str.sprintf("%.0f째", desire_angle);
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
  str.sprintf("%.0f째C", cpuTemp);
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
  str.sprintf("%.0f째C", ambientTemp);
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


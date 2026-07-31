#include "selfdrive/ui/qt/onroad.h"

#include <cmath>
#include <cstdlib>
#include <string>

#include <QDebug>
#include <QSound>
#include <QMouseEvent>
#include <QDateTime>

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

  // ── MyDrivingMode 탭 전환 (좌하단 모드 박스) ────────────
  //    모드 박스는 NvgWindow 기준 x 35~145, y (h-93)~(h-45).
  //    NvgWindow 가 bdr_s 만큼 안쪽에 있으므로 OnroadWindow 기준으로는
  //    거의 같은 위치가 된다. 탭하기 쉽게 여유를 둔다.
  {
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
  }

  // ── ChevronInfo 탭 토글 ──────────────────────────────
  {
    int tap_x = endPos.x();
    int tap_y = endPos.y();
    int center_x = width() / 2;
    int bottom_y = height() * 2 / 3;

    if (std::abs(tap_x - center_x) < 200 && tap_y > bottom_y) {
      int cur = std::atoi(Params().get("ChevronInfo").c_str());
      int next = (cur + 1) % 5;  // 0(Off)→1(Dist)→2(Spd)→3(TTC)→4(All)→0
      Params().put("ChevronInfo", std::to_string(next));
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

  //Blind Spot Warnings
  painter.setPen(Qt::NoPen);

  // 왼쪽 barrier: 감지=빨강(alpha 0.45), 평상시=흰색(alpha 0.10)
  painter.setBrush(left_blindspot
      ? QColor::fromRgbF(1.0, 0.0, 0.0, 0.45)
      : QColor::fromRgbF(1.0, 1.0, 1.0, 0.10));
  painter.drawPolygon(scene.lane_barrier_vertices[0].v, scene.lane_barrier_vertices[0].cnt);

  // 오른쪽 barrier: 감지=빨강(alpha 0.45), 평상시=흰색(alpha 0.10)
  painter.setBrush(right_blindspot
      ? QColor::fromRgbF(1.0, 0.0, 0.0, 0.45)
      : QColor::fromRgbF(1.0, 1.0, 1.0, 0.10));
  painter.drawPolygon(scene.lane_barrier_vertices[1].v, scene.lane_barrier_vertices[1].cnt);
	
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

void NvgWindow::drawLead(QPainter &painter, const cereal::ModelDataV2::LeadDataV3::Reader &lead_data, const QPointF &vd, bool is_radar) {
  painter.save();
  const float speedBuff = 10.;
  const float leadBuff = 40.;
  const float d_rel = lead_data.getX()[0];
  const float v_rel = lead_data.getV()[0];

  float fillAlpha = 0;
  if (d_rel < leadBuff) {
    fillAlpha = 255 * (1.0 - (d_rel / leadBuff));
    if (v_rel < 0) {
      fillAlpha += 255 * (-1 * (v_rel / speedBuff));
    }
    fillAlpha = (int)(fmin(fillAlpha, 255));
  }

  float sz = std::clamp((25 * 30) / (d_rel / 3 + 30), 15.0f, 30.0f) * 2.35;
  float x = std::clamp((float)vd.x(), 0.f, width() - sz / 2);
  float y = std::fmin(height() - sz * .6, (float)vd.y());

  float g_xo = sz / 5;
  float g_yo = sz / 10;

  QPointF glow[] = {{x + (sz * 1.35) + g_xo, y + sz + g_yo}, {x, y - g_yo}, {x - (sz * 1.35) - g_xo, y + sz + g_yo}};
  painter.setBrush(is_radar ? QColor(86, 121, 216, 255) : QColor(218, 202, 37, 255));
  painter.drawPolygon(glow, std::size(glow));

  // chevron
  QPointF chevron[] = {{x + (sz * 1.25), y + sz}, {x, y}, {x - (sz * 1.25), y + sz}};
  painter.setBrush(redColor(fillAlpha));
  painter.drawPolygon(chevron, std::size(chevron));

  painter.restore();
}

void NvgWindow::drawLeadStatus(QPainter &p) {
  UIState *s = uiState();
  auto &sm = *(s->sm);

  if (!sm.updated("radarState")) return;

  const auto &radar_state = sm["radarState"].getRadarState();
  const auto &lead_one    = radar_state.getLeadOne();
  const auto &lead_two    = radar_state.getLeadTwo();

  bool has_lead_one = lead_one.getStatus();
  bool has_lead_two = lead_two.getStatus();

  // 리드 없으면 서서히 페이드아웃
  if (!has_lead_one && !has_lead_two) {
    lead_status_alpha = std::max(0.0f, lead_status_alpha - 0.05f);
    if (lead_status_alpha <= 0.0f) return;
  } else {
    lead_status_alpha = std::min(1.0f, lead_status_alpha + 0.1f);
  }

  // L1: 항상 시도 (has_lead_one 여부 무관하게 위치 유지)
  if (true) {
    drawLeadStatusAtPosition(p, lead_one, s->scene.lead_vertices[0], "L1");
  }

  // L2: 두 리드 거리 차이 3m 이상일 때만
  if (has_lead_two &&
      std::abs(lead_one.getDRel() - lead_two.getDRel()) > 3.0) {
    drawLeadStatusAtPosition(p, lead_two, s->scene.lead_vertices[1], "L2");
  }
}

void NvgWindow::drawLeadStatusAtPosition(QPainter &p,
                                         const cereal::RadarState::LeadData::Reader &lead_data,
                                         const QPointF &chevron_pos,
                                         const QString &label) {
  UIState *s = uiState();
  auto &sm   = *(s->sm);

  float d_rel = lead_data.getDRel();
  float v_rel = lead_data.getVRel();
  float v_ego = sm["carState"].getCarState().getVEgo();
  bool is_metric = s->scene.is_metric;

  // 파라미터에서 표시 모드 읽기 (0=Off, 1=Distance, 2=Speed, 3=Time, 4=All)
  int chevron_data = std::atoi(Params().get("ChevronInfo").c_str());
  if (chevron_data == 0) return;

  // 쉐브론 크기 (drawLead 와 동일 로직)
  float sz = std::clamp((25 * 30) / (d_rel / 3 + 30), 15.0f, 30.0f) * 2.35;

  QFont content_font = p.font();
  content_font.setPixelSize(45);
  content_font.setBold(true);
  p.setFont(content_font);

  const int chevron_types = 3;
  const int chevron_all   = chevron_types + 1; // value 4 = All

  QStringList chevron_text[chevron_types];

  // 1) Distance
  if (chevron_data == 1 || chevron_data == chevron_all) {
    float val = std::max(0.0f, d_rel);
    QString unit = is_metric ? "m" : "ft";
    if (!is_metric) val *= 3.28084f;
    chevron_text[0].append(QString::number(val, 'f', 0) + " " + unit);
  }

  // 2) Absolute speed
  if (chevron_data == 2 || chevron_data == chevron_all) {
    int pos = (chevron_data == 2) ? 0 : 1;
    float val = std::max(0.0f,
        (v_rel + v_ego) * (is_metric ? static_cast<float>(MS_TO_KPH)
                                     : static_cast<float>(MS_TO_MPH)));
    chevron_text[pos].append(QString::number(val, 'f', 0) + " " +
                             (is_metric ? "km/h" : "mph"));
  }

  // 3) Time-to-contact
  if (chevron_data == 3 || chevron_data == chevron_all) {
    int pos = (chevron_data == 3) ? 0 : 2;
    float val = (d_rel > 0 && v_ego > 0)
                    ? std::max(0.0f, d_rel / v_ego)
                    : 0.0f;
    QString ttc_str = (val > 0 && val < 200)
                          ? QString::number(val, 'f', 1) + "s"
                          : "---";
    chevron_text[pos].append(ttc_str);
  }

  // 빈 항목 제거하여 최종 라인 목록 생성
  QStringList text_lines;
  for (int i = 0; i < chevron_types; ++i) {
    if (!chevron_text[i].isEmpty())
      text_lines.append(chevron_text[i]);
  }
  if (text_lines.isEmpty()) return;

  // 텍스트 박스 위치 (쉐브론 아래 중앙)
  const float str_w = 190.0f;
  const float str_h = 58.0f;
  float text_x = chevron_pos.x() - str_w / 2;
  float text_y = chevron_pos.y() + sz + 15;

  // 화면 경계 클램핑
  text_x = std::clamp(text_x, 10.0f, (float)width() - str_w - 10);

  QPoint shadow_offset(2, 2);

  for (int i = 0; i < text_lines.size(); ++i) {
    const QString &line = text_lines[i];
    if (line.isEmpty()) continue;

    QRect textRect((int)text_x,
                   (int)(text_y + i * str_h),
                   (int)str_w,
                   (int)str_h);

    // 그림자
    p.setPen(QColor(0, 0, 0, (int)(200 * lead_status_alpha)));
    p.drawText(textRect.translated(shadow_offset.x(), shadow_offset.y()),
               Qt::AlignBottom | Qt::AlignHCenter, line);

    // 본문 색상 결정
    QColor text_color = QColor(255, 255, 255, (int)(255 * lead_status_alpha));

    p.setPen(text_color);
    p.drawText(textRect, Qt::AlignBottom | Qt::AlignHCenter, line);
  }

  p.setPen(Qt::NoPen);
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

  drawLaneLines(p, s);

  auto leads =  model.getLeadsV3();
  if (leads[0].getProb() > .5) {
    drawLead(p, leads[0], s->scene.lead_vertices[0], s->scene.lead_radar[0]);
  }
  if (leads[1].getProb() > .5 && (std::abs(leads[1].getX()[0] - leads[0].getX()[0]) > 3.0)) {
    drawLead(p, leads[1], s->scene.lead_vertices[1], s->scene.lead_radar[1]);
  }

  drawLeadStatus(p);
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

// =====================================================================================
//  CarrotPilot style HUD panel
//  ajouatom/openpilot (c3-wip) selfdrive/ui/carrot.cc :: drawHud() 을
//  이 fork 의 QPainter 기반 UI 로 이식한 것.
//  NanoVG(ui_draw_text / ui_fill_rect) -> QPainter(drawText / drawRoundedRect)
// =====================================================================================

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

// 박스 사각형 안에 글자를 정중앙(대문자 기준 광학 중심)으로 배치한다.
// Qt 의 AlignCenter 는 descent 공간까지 포함해 중앙을 잡기 때문에
// 숫자/대문자만 있는 문자열은 살짝 위로 떠 보인다. capHeight 로 보정한다.
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

// carrot.cc BorderDrawer 의 top_left / top_right 정보줄을 이 fork 로 옮긴 것.
//  좌상단 : 차량명 + OP Long 여부 + SCC 버스 상태
//  우상단 : 토크 학습값(LT) + 스티어레이시오(SR)
void NvgWindow::drawCarrotInfo(QPainter &p) {
  p.save();

  const SubMaster &sm = *(uiState()->sm);
  const auto car_params      = sm["carParams"].getCarParams();
  const auto controls_state  = sm["controlsState"].getControlsState();
  const auto live_params     = sm["liveParameters"].getLiveParameters();
  const auto torque_state    = controls_state.getLateralControlState().getTorqueState();

  // ---- 좌상단 : 차량명 / SCC ----
  QString car_name = QString::fromUtf8(car_params.getCarFingerprint().cStr());
  if (car_name.isEmpty()) car_name = "UNKNOWN";
  if (car_params.getOpenpilotLongitudinalControl()) car_name += " - OP Long";

  int scc_bus = (int)car_params.getSccBus();
  QString scc_num  = (scc_bus < 0) ? QString("none") : QString::number(scc_bus);
  QString scc_tail = QString(car_params.getHasScc13() ? "+13" : "") +
                     QString(car_params.getHasScc14() ? "+14" : "");
  QString left_head = car_name + "  |  SCC ";

  // ---- 우상단 : 토크값 / SR ----
  QString right_str;
  right_str.sprintf("LT(%.2f/%.3f)  SR(%.2f/%.2f)",
                    torque_state.getLatAccelFactor(),
                    torque_state.getFriction(),
                    controls_state.getSteerRatio(),
                    live_params.getSteerRatio());

  configFont(p, "Open Sans", 34, "Regular");
  QRect line(20, 12, width() - 40, 48);   // 상태바(bdr_s=20) 안쪽

  // 우상단은 한 덩어리
  p.setPen(QColor(0xff, 0xff, 0xff, 200));
  p.drawText(line, Qt::AlignRight | Qt::AlignVCenter, right_str);

  // 좌상단은 SCC 숫자만 색이 달라서 조각내어 그린다
  {
    QFontMetrics fm(p.font());
    int cap = fm.capHeight();
    if (cap <= 0) cap = (int)(fm.ascent() * 0.72f);
    int base_y = line.center().y() + cap / 2;
    int tx = line.x();

    QString name_part = left_head;
    name_part.chop(4);                          // 뒤쪽 "SCC " 분리
    p.setPen(QColor(0xff, 0xff, 0xff, 200));
    p.drawText(tx, base_y, name_part);
    tx += fm.horizontalAdvance(name_part);

    p.setPen(QColor(190, 0, 0, 255));           // "SCC" 는 진한 빨강
    p.drawText(tx, base_y, "SCC ");
    tx += fm.horizontalAdvance("SCC ");

    p.setPen(QColor(0xff, 0xff, 0xff, 200));    // 숫자는 흰색
    p.drawText(tx, base_y, scc_num);
    tx += fm.horizontalAdvance(scc_num);

    if (!scc_tail.isEmpty()) {
      p.setPen(QColor(0xff, 0xff, 0xff, 200));
      p.drawText(tx, base_y, scc_tail);
    }
  }

  p.restore();
}

// 화면 우하단 : wifi IP
// 녹화버튼(190px, 우하단 여백 35)과 겹치지 않게 폭을 줄여둔다.
void NvgWindow::drawCarrotBottom(QPainter &p) {
  p.save();

  const SubMaster &sm = *(uiState()->sm);
  QString ip = QString::fromUtf8(sm["deviceState"].getDeviceState().getWifiIpAddress().cStr());

  configFont(p, "Open Sans", 34, "Regular");
  p.setPen(QColor(0xff, 0xff, 0xff, 200));

  QRect line(20, height() - 62, width() - 40 - 240, 48);
  p.drawText(line, Qt::AlignRight | Qt::AlignVCenter, ip);

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
    std::string sdt = params.get("ShowDateTime");
    show_datetime = sdt.empty() ? 1 : std::atoi(sdt.c_str());   // 0:끔 1:시간+날짜 2:시간만 3:날짜만
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

  // ---- 차간거리(GAP) 숫자 + 막대 ----
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
  }

  // ---- NDA / HDA (carrot 의 APN/APM 자리. roadLimitSpeed.active) ----
  //      active == 1 : 일반도로 속도정보 수신(NDA)
  //      active >= 2 : 고속도로/자동차전용도로(HDA)
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

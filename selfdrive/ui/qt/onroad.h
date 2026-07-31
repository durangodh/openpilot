#pragma once
#include <QStackedLayout>
#include <QWidget>
#include "selfdrive/common/util.h"
#include "selfdrive/ui/qt/widgets/cameraview.h"
#include "selfdrive/ui/ui.h"
#include <QTimer>
#include <QMap>
#include <QPointF>
#include <QVector>
#include "selfdrive/ui/qt/screenrecorder/screenrecorder.h"

// ***** onroad widgets *****
class OnroadAlerts : public QWidget {
  Q_OBJECT
public:
  OnroadAlerts(QWidget *parent = 0) : QWidget(parent) {};
  void updateAlert(const Alert &a, const QColor &color);
protected:
  void paintEvent(QPaintEvent*) override;
private:
  QColor bg;
  Alert alert = {};
};

// container window for the NVG UI
class NvgWindow : public CameraViewWidget {
  Q_OBJECT
  Q_PROPERTY(bool engageable MEMBER engageable NOTIFY valueChanged);
  Q_PROPERTY(bool experimentalMode MEMBER experimentalMode NOTIFY valueChanged);
  Q_PROPERTY(int status MEMBER status NOTIFY valueChanged);
  Q_PROPERTY(float ang_str MEMBER ang_str NOTIFY valueChanged);
  Q_PROPERTY(bool left_blindspot MEMBER left_blindspot);   // blind spot
  Q_PROPERTY(bool right_blindspot MEMBER right_blindspot); // blind spot

public:
  explicit NvgWindow(VisionStreamType type, QWidget* parent = 0);
  void updateState(const UIState &s);

signals:
  void valueChanged();

protected:
  void paintGL() override;
  void initializeGL() override;
  void showEvent(QShowEvent *event) override;
  void updateFrameMat(int w, int h) override;
  void drawLaneLines(QPainter &painter, const UIState *s);
  // carrot 諛⑹떇 BSD : 踰쎌쓣 議곌컖?댁뼱 ?명?由ъ쿂??洹몃┛??
  void drawBlindSpot(QPainter &painter, const line_vertices_data &vd, const QColor &color);
  // carrot ?ㅽ???由щ뱶 ?쒖떆 (湲곗〈 ?먮툕濡?/ ChevronInfo ?泥?
  void drawCarrotLead(QPainter &p);

  inline QColor redColor(int alpha = 255)   { return QColor(201, 34, 49, alpha); }
  inline QColor whiteColor(int alpha = 255) { return QColor(255, 255, 255, alpha); }
  inline QColor blackColor(int alpha = 255) { return QColor(0, 0, 0, alpha); }

  double prev_draw_t = 0;
  bool engageable = false;
  bool experimentalMode = false;
  int status = STATUS_DISENGAGED;
  float ang_str = 0;

  bool left_blindspot  = false; // blind spot
  bool right_blindspot = false; // blind spot

  FirstOrderFilter fps_filter;
  FirstOrderFilter accel_filter;

  // neokii
  void drawIcon(QPainter &p, int x, int y, QPixmap &img, QBrush bg, float opacity,
                bool rotation = false, float angle = 0.0f);
  void drawText(QPainter &p, int x, int y, const QString &text, int alpha = 255);
  void drawText2(QPainter &p, int x, int y, int flags, const QString &text, const QColor &color);
  void drawTextWithColor(QPainter &p, int x, int y, const QString &text, QColor &color);
  void paintEvent(QPaintEvent *event) override;

  const int radius   = 192;
  const int img_size = (radius / 2) * 1.5;
  uint64_t last_update_params;

  QPixmap engage_img;
  QPixmap experimental_img;

  // neokii
  QPixmap ic_brake;
  QPixmap ic_autohold_warning;
  QPixmap ic_autohold_active;
  QPixmap ic_nda;
  QPixmap ic_hda;
  QPixmap ic_tire_pressure;
  QPixmap ic_turn_signal_l;
  QPixmap ic_turn_signal_r;
  QPixmap ic_satellite;

  void drawMaxSpeed(QPainter &p);
  void drawSpeed(QPainter &p);
  void drawBottomIcons(QPainter &p);

  // ===== CarrotPilot style HUD (ported from ajouatom/openpilot c3-wip : carrot.cc drawHud) =====
  void drawCarrotHud(QPainter &p);
  void ctRect(QPainter &p, const QRect &r, const QColor &fill, int corner,
              int borderWidth = 0, const QColor &borderColor = QColor(255, 255, 255, 255));
  void ctText(QPainter &p, int x, int y, const QString &text, int size,
              const QColor &color, bool bold = true, bool shadow = false);
  // 諛뺤뒪 ?덉뿉 ?곹븯醫뚯슦 ?뺤쨷?숈쑝濡?湲??諛곗튂
  void ctTextIn(QPainter &p, const QRect &box, const QString &text, int size,
                const QColor &color, bool bold = true);
  // ?붾㈃ 理쒖긽??醫????뺣낫以?(carrot ??top_left / top_right ? ?숈씪 ?꾩튂)
  void drawCarrotInfo(QPainter &p);
  // ?붾㈃ ?고븯???뺣낫以?(wifi IP)
  void drawCarrotBottom(QPainter &p);
  void drawCarrotNavi(QPainter &p);
  void updateCarrotNavi();

  QPixmap ic_speed_bg;
  int  blink_timer = 0;
  int  carrot_param_timer = 0;
  int  my_driving_mode = 3;
  int  show_device_state = 0;
  int  show_datetime = 1;
  int  show_gear_animation = 1;
  int  show_bsd_always = 0;
  uint64_t carrot_navi_last_read = 0;
  uint64_t carrot_navi_updated_at = 0;
  QVector<QPointF> carrot_navi_route;
  double carrot_navi_lat = 0.0;
  double carrot_navi_lon = 0.0;
  QString carrot_navi_road;
  QString carrot_navi_instruction;
  int carrot_navi_distance = -1;
  int carrot_navi_remain_distance = -1;
  int carrot_navi_remain_time = -1;
  float lead_box_w = 0.0f, lead_box_x = 0.0f, lead_box_y = 0.0f;   // 由щ뱶諛뺤뒪 EMA

  // ?? ?앹뾽 ?좊땲硫붿씠??(carrot ui_draw_text_a ?댁떇) ??
  void ctTextAnimStart(int x, int y, const QString &text, int size, const QColor &color);
  void drawTextAnim(QPainter &p);
  int     anim_time = 0;        // 0 ?대㈃ 鍮꾪솢??
  int     anim_x = 0, anim_y = 0, anim_size = 0;
  QString anim_text;
  QColor  anim_color = QColor(255, 255, 255, 255);
  QString gear_str_last;
  // ============================================================================
  void drawSpeedLimit(QPainter &p);
  void drawSteer(QPainter &p);
  void drawThermal(QPainter &p);
  void drawTurnSignals(QPainter &p);
  void drawGpsStatus(QPainter &p);
  void drawDebugText(QPainter &p);
  void drawHud(QPainter &p, const cereal::ModelDataV2::Reader &model);
};

// container for all onroad widgets
class OnroadWindow : public QWidget {
  Q_OBJECT
public:
  OnroadWindow(QWidget* parent = 0);
  bool isMapVisible() const { return map && map->isVisible(); }

protected:
  void mousePressEvent(QMouseEvent* e) override;
  void mouseReleaseEvent(QMouseEvent* e) override;
  void paintEvent(QPaintEvent *event) override;

private:
  OnroadAlerts *alerts;
  NvgWindow *nvg;
  QColor bg = bg_colors[STATUS_DISENGAGED];
  QWidget *map = nullptr;
  QHBoxLayout *split;

  // neokii
  ScreenRecoder *recorder;
  std::shared_ptr<QTimer> record_timer;
  QPoint startPos;

  Params params;

private slots:
  void offroadTransition(bool offroad);
  void updateState(const UIState &s);
};


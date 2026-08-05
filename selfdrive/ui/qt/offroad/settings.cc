#include "selfdrive/ui/qt/offroad/settings.h"

#include <algorithm>
#include <array>
#include <cassert>
#include <cmath>
#include <map>
#include <string>
#include <tuple>

#include <QDebug>

#ifndef QCOM
#include "selfdrive/ui/qt/offroad/networking.h"
#endif

#ifdef ENABLE_MAPS
#include "selfdrive/ui/qt/maps/map_settings.h"
#endif

#include "selfdrive/common/params.h"
#include "selfdrive/common/util.h"
#include "selfdrive/hardware/hw.h"
#include "selfdrive/ui/qt/widgets/controls.h"
#include "selfdrive/ui/qt/widgets/input.h"
#include "selfdrive/ui/qt/widgets/scrollview.h"
#include "selfdrive/ui/qt/widgets/ssh_keys.h"
#include "selfdrive/ui/qt/widgets/toggle.h"
#include "selfdrive/ui/ui.h"
#include "selfdrive/ui/qt/util.h"
#include "selfdrive/ui/qt/qt_window.h"

#include <QComboBox>
#include <QAbstractItemView>
#include <QScroller>
#include <QListView>
#include <QListWidget>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QPainter>
#include <QPainterPath>
#include <QMouseEvent>
#include <QDateTime>
#include <QSet>
#include <QStringList>
#include <QFile>
#include <QDir>

// ── Offset Total Control ─────────────────────────────────────────
OffsetTotalControl::OffsetTotalControl(const QString &title,
                                       const QString &desc,
                                       const QString &icon,
                                       QWidget *parent)
    : AbstractControl(title, desc, icon, parent) {

  QWidget *btn_widget = new QWidget();
  QHBoxLayout *btn_layout = new QHBoxLayout(btn_widget);
  btn_layout->setContentsMargins(0, 8, 0, 8);
  btn_layout->setSpacing(12);

  const QString btn_style = R"(
    QPushButton {
      font-size: 48px;
      font-weight: bold;
      border-radius: 14px;
      background-color: #393939;
      color: #ffffff;
      min-width: 150px;
      max-width: 150px;
      min-height: 100px;
      max-height: 100px;
    }
    QPushButton:pressed { background-color: #4a4a4a; }
  )";

  minus_btn = new QPushButton("−");
  minus_btn->setStyleSheet(btn_style);
  connect(minus_btn, &QPushButton::clicked, [=]() { changeValue(-1); });

  value_label = new QLabel();
  value_label->setAlignment(Qt::AlignCenter);
  value_label->setStyleSheet("font-size: 40px; color: #ffffff; min-width: 140px;");

  plus_btn = new QPushButton("+");
  plus_btn->setStyleSheet(btn_style);
  connect(plus_btn, &QPushButton::clicked, [=]() { changeValue(+1); });

  btn_layout->addStretch();
  btn_layout->addWidget(minus_btn);
  btn_layout->addWidget(value_label);
  btn_layout->addWidget(plus_btn);

  qobject_cast<QVBoxLayout*>(layout())->addWidget(btn_widget);
  refresh();
}

void OffsetTotalControl::changeValue(int delta) {
  std::string raw = params.get("OffsetTotal");
  double val = raw.empty() ? 0.0 : std::stod(raw);
  val += delta * 0.01;
  val = std::round(val * 100.0) / 100.0;
  val = std::max(-1.00, std::min(1.00, val));
  params.put("OffsetTotal", std::to_string(val));
  refresh();
}

void OffsetTotalControl::refresh() {
  std::string raw = params.get("OffsetTotal");
  double val = raw.empty() ? 0.0 : std::stod(raw);
  val = std::round(val * 100.0) / 100.0;
  value_label->setText(QString::number(val, 'f', 2) + " m");
  minus_btn->setEnabled(val > -1.00);
  plus_btn->setEnabled(val < 1.00);
}

// ── AdjustLaneOffset Control ─────────────────────────────────────
AdjustLaneOffsetControl::AdjustLaneOffsetControl(const QString &title,
                                                 const QString &desc,
                                                 const QString &icon,
                                                 QWidget *parent)
    : AbstractControl(title, desc, icon, parent) {

  QWidget *btn_widget = new QWidget();
  QHBoxLayout *btn_layout = new QHBoxLayout(btn_widget);
  btn_layout->setContentsMargins(0, 8, 0, 8);
  btn_layout->setSpacing(12);

  const QString btn_style = R"(
    QPushButton {
      font-size: 48px;
      font-weight: bold;
      border-radius: 14px;
      background-color: #393939;
      color: #ffffff;
      min-width: 150px;
      max-width: 150px;
      min-height: 100px;
      max-height: 100px;
    }
    QPushButton:pressed { background-color: #4a4a4a; }
  )";

  minus_btn = new QPushButton("−");
  minus_btn->setStyleSheet(btn_style);
  connect(minus_btn, &QPushButton::clicked, [=]() { changeValue(-1); });

  value_label = new QLabel();
  value_label->setAlignment(Qt::AlignCenter);
  value_label->setStyleSheet("font-size: 40px; color: #ffffff; min-width: 140px;");

  plus_btn = new QPushButton("+");
  plus_btn->setStyleSheet(btn_style);
  connect(plus_btn, &QPushButton::clicked, [=]() { changeValue(+1); });

  btn_layout->addStretch();
  btn_layout->addWidget(minus_btn);
  btn_layout->addWidget(value_label);
  btn_layout->addWidget(plus_btn);

  qobject_cast<QVBoxLayout*>(layout())->addWidget(btn_widget);
  refresh();
}

void AdjustLaneOffsetControl::changeValue(int delta) {
  int val = std::atoi(params.get("AdjustLaneOffset").c_str());
  val += delta * 5;                       // 5cm 단위
  val = std::max(0, std::min(40, val));   // 0 ~ 40cm (내부 클리핑 0.4m)
  params.put("AdjustLaneOffset", std::to_string(val));
  refresh();
}

void AdjustLaneOffsetControl::refresh() {
  int val = std::atoi(params.get("AdjustLaneOffset").c_str());
  val = std::max(0, std::min(40, val));
  value_label->setText(val == 0 ? "OFF" : QString::number(val) + " cm");
  minus_btn->setEnabled(val > 0);
  plus_btn->setEnabled(val < 40);
}

// ── AutoLaneChangeTimer Control ─────────────────────────────────
AutoLaneChangeTimerControl::AutoLaneChangeTimerControl(const QString &title,
                                                       const QString &desc,
                                                       const QString &icon,
                                                       QWidget *parent)
    : AbstractControl(title, desc, icon, parent) {

  // 버튼을 제목 아래에 배치
  QWidget *btn_widget = new QWidget();
  QHBoxLayout *btn_layout = new QHBoxLayout(btn_widget);
  btn_layout->setContentsMargins(0, 8, 0, 8);
  btn_layout->setSpacing(8);

  for (int i = 0; i < labels.size(); i++) {
    buttons[i] = new QPushButton(labels[i]);
    buttons[i]->setFixedHeight(70);
    buttons[i]->setCheckable(true);
    buttons[i]->setStyleSheet(R"(
      QPushButton {
        font-size: 30px;
        border-radius: 10px;
        background-color: #393939;
        color: #aaaaaa;
      }
      QPushButton:checked {
        background-color: #0064ff;
        color: #ffffff;
      }
      QPushButton:pressed {
        background-color: #4a4a4a;
      }
    )");

    connect(buttons[i], &QPushButton::clicked, [=]() {
      params.put("AutoLaneChangeTimer", std::to_string(i));
      refresh();
    });

    btn_layout->addWidget(buttons[i]);
  }

  qobject_cast<QVBoxLayout*>(layout())->addWidget(btn_widget);
  refresh();
}

void AutoLaneChangeTimerControl::refresh() {
  int val = std::atoi(params.get("AutoLaneChangeTimer").c_str());
  val = std::clamp(val, 0, (int)labels.size() - 1);
  for (int i = 0; i < labels.size(); i++) {
    buttons[i]->setChecked(i == val);
  }
}

// ── DynamicLaneProfile Control ──────────────────────────────────
DynamicLaneProfileControl::DynamicLaneProfileControl(const QString &title,
                                                     const QString &desc,
                                                     const QString &icon,
                                                     QWidget *parent)
    : AbstractControl(title, desc, icon, parent) {

  // 버튼을 제목 아래에 배치
  QWidget *btn_widget = new QWidget();
  QHBoxLayout *btn_layout = new QHBoxLayout(btn_widget);
  btn_layout->setContentsMargins(0, 8, 0, 8);
  btn_layout->setSpacing(8);

  for (int i = 0; i < labels.size(); i++) {
    buttons[i] = new QPushButton(labels[i]);
    buttons[i]->setFixedHeight(70);
    buttons[i]->setCheckable(true);
    buttons[i]->setStyleSheet(R"(
      QPushButton {
        font-size: 30px;
        border-radius: 10px;
        background-color: #393939;
        color: #aaaaaa;
      }
      QPushButton:checked {
        background-color: #0064ff;
        color: #ffffff;
      }
      QPushButton:pressed {
        background-color: #4a4a4a;
      }
    )");

    connect(buttons[i], &QPushButton::clicked, [=]() {
      params.put("DynamicLaneProfile", std::to_string(i));
      refresh();
    });

    btn_layout->addWidget(buttons[i]);
  }

  qobject_cast<QVBoxLayout*>(layout())->addWidget(btn_widget);
  refresh();
}

void DynamicLaneProfileControl::refresh() {
  int val = std::atoi(params.get("DynamicLaneProfile").c_str());
  val = std::clamp(val, 0, (int)labels.size() - 1);
  for (int i = 0; i < labels.size(); i++) {
    buttons[i]->setChecked(i == val);
  }
}

// ── CarrotPilot Auto-Tuner (commit 9dd5e2c port) ─────────────────────────
// nTune 토크 파일 헬퍼: latcontrol_torque가 /data/ntune/lat_torque*.json 을
// 라이브 리로드하므로 토크 파라미터 복원은 이 파일에 기록
static QString findNtuneTorqueFileS() {
  QDir dir("/data/ntune");
  const QStringList files = dir.entryList({"lat_torque*.json"}, QDir::Files, QDir::Name);
  if (!files.isEmpty()) return dir.filePath(files.first());
  return "/data/ntune/lat_torque_v4.json";
}

static void writeNtuneTorqueValueS(const QString &key, double value) {
  QString path = findNtuneTorqueFileS();
  QJsonObject obj;
  QFile f(path);
  if (f.open(QIODevice::ReadOnly)) {
    obj = QJsonDocument::fromJson(f.readAll()).object();
    f.close();
  }
  obj[key] = value;
  QDir().mkpath("/data/ntune");
  if (f.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
    f.write(QJsonDocument(obj).toJson(QJsonDocument::Indented));
    f.close();
    // nTune.write_config와 동일하게 0666 권한 유지
    f.setPermissions(QFileDevice::ReadOwner | QFileDevice::WriteOwner |
                     QFileDevice::ReadGroup | QFileDevice::WriteGroup |
                     QFileDevice::ReadOther | QFileDevice::WriteOther);
  }
}

static void writeNtuneCommonValueS(const QString &key, double value) {
  QString path = "/data/ntune/common.json";
  QJsonObject obj;
  QFile f(path);
  if (f.open(QIODevice::ReadOnly)) {
    obj = QJsonDocument::fromJson(f.readAll()).object();
    f.close();
  }
  obj[key] = value;
  QDir().mkpath("/data/ntune");
  if (f.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
    f.write(QJsonDocument(obj).toJson(QJsonDocument::Indented));
    f.close();
    f.setPermissions(QFileDevice::ReadOwner | QFileDevice::WriteOwner |
                     QFileDevice::ReadGroup | QFileDevice::WriteGroup |
                     QFileDevice::ReadOther | QFileDevice::WriteOther);
  }
}

// 토크값(latAccelFactor/friction)은 nTune JSON 이 아니라 Params 에 있다.
static const std::map<QString, std::pair<QString, double>> kTorqueParamKey = {
  {"latAccelFactor", {"LateralTorqueAccelFactor", 1000.0}},
  {"friction",       {"LateralTorqueFriction",    1000.0}},
};

static bool torqueParamOf(const QString &key, QString *pkey, double *scale) {
  auto it = kTorqueParamKey.find(key);
  if (it == kTorqueParamKey.end()) return false;
  *pkey = it->second.first;
  *scale = it->second.second;
  return true;
}

static double readNtuneValueS(const QString &group, const QString &key, double def) {
  QString pkey; double scale;
  if (group == "torque" && torqueParamOf(key, &pkey, &scale)) {
    std::string v = Params().get(pkey.toStdString());
    return v.empty() ? def : std::atof(v.c_str()) / scale;
  }
  QString path = (group == "torque") ? findNtuneTorqueFileS() : "/data/ntune/common.json";
  QFile f(path);
  if (!f.open(QIODevice::ReadOnly)) return def;
  QJsonObject obj = QJsonDocument::fromJson(f.readAll()).object();
  f.close();
  return obj.contains(key) ? obj[key].toDouble(def) : def;
}

// ── Param Value Control (정수 Params) ────────────────────────────
ParamValueControlF::ParamValueControlF(const QString &param, const QString &title, const QString &desc,
                                       const QString &icon, int vmin, int vmax, int step, int decimals,
                                       int vdefault, QWidget *parent)
    : AbstractControl(title, desc, icon, parent),
      param_(param), vmin_(vmin), vmax_(vmax), step_(step), decimals_(decimals), vdefault_(vdefault) {

  QWidget *btn_widget = new QWidget();
  QHBoxLayout *btn_layout = new QHBoxLayout(btn_widget);
  btn_layout->setContentsMargins(0, 8, 0, 8);
  btn_layout->setSpacing(12);

  const QString btn_style = R"(
    QPushButton {
      font-size: 48px;
      font-weight: bold;
      border-radius: 14px;
      background-color: #393939;
      color: #ffffff;
      min-width: 150px;
      max-width: 150px;
      min-height: 100px;
      max-height: 100px;
    }
    QPushButton:pressed { background-color: #4a4a4a; }
  )";

  minus_btn = new QPushButton("−");
  minus_btn->setStyleSheet(btn_style);
  connect(minus_btn, &QPushButton::clicked, [=]() { changeValue(-1); });

  value_label = new QLabel();
  value_label->setAlignment(Qt::AlignCenter);
  value_label->setStyleSheet("font-size: 40px; color: #ffffff; min-width: 170px;");

  plus_btn = new QPushButton("+");
  plus_btn->setStyleSheet(btn_style);
  connect(plus_btn, &QPushButton::clicked, [=]() { changeValue(+1); });

  btn_layout->addStretch();
  btn_layout->addWidget(minus_btn);
  btn_layout->addWidget(value_label);
  btn_layout->addWidget(plus_btn);

  qobject_cast<QVBoxLayout*>(layout())->addWidget(btn_widget);
  refresh();
}

void ParamValueControlF::changeValue(int delta) {
  std::string cur = params.get(param_.toStdString());
  int v = cur.empty() ? ((param_ == "E2EAccMode" && params.getBool("ExperimentalMode")) ? 2 : vdefault_) : std::atoi(cur.c_str());
  v = std::max(vmin_, std::min(vmax_, v + delta * step_));
  params.put(param_.toStdString(), std::to_string(v));
  if (param_ == "E2EAccMode") {
    params.putBool("ExperimentalMode", v == 2);
  }
  refresh();
}

void ParamValueControlF::refresh() {
  std::string cur = params.get(param_.toStdString());
  int v = cur.empty() ? ((param_ == "E2EAccMode" && params.getBool("ExperimentalMode")) ? 2 : vdefault_) : std::atoi(cur.c_str());
  v = std::max(vmin_, std::min(vmax_, v));
  if (param_ == "E2EAccMode") {
    static const QStringList modes = {"ACC", "AUTO", "E2E"};
    value_label->setText(modes[v]);
  } else if (vmin_ == 0 && vmax_ == 1) {
    value_label->setText(v > 0 ? "ON" : "OFF");
  } else {
    value_label->setText(QString::number(v));
  }
  minus_btn->setEnabled(v > vmin_);
  plus_btn->setEnabled(v < vmax_);
}

// ── nTune Value Control ──────────────────────────────────────────
NtuneValueControl::NtuneValueControl(const QString &group, const QString &key,
                                     const QString &title, const QString &desc, const QString &icon,
                                     double vmin, double vmax, double step, int decimals,
                                     double vdefault, QWidget *parent)
    : AbstractControl(title, desc, icon, parent),
      group_(group), key_(key), vmin_(vmin), vmax_(vmax), step_(step),
      vdefault_(vdefault), decimals_(decimals) {

  QWidget *btn_widget = new QWidget();
  QHBoxLayout *btn_layout = new QHBoxLayout(btn_widget);
  btn_layout->setContentsMargins(0, 8, 0, 8);
  btn_layout->setSpacing(12);

  const QString btn_style = R"(
    QPushButton {
      font-size: 48px;
      font-weight: bold;
      border-radius: 14px;
      background-color: #393939;
      color: #ffffff;
      min-width: 150px;
      max-width: 150px;
      min-height: 100px;
      max-height: 100px;
    }
    QPushButton:pressed { background-color: #4a4a4a; }
  )";

  minus_btn = new QPushButton("−");
  minus_btn->setStyleSheet(btn_style);
  connect(minus_btn, &QPushButton::clicked, [=]() { changeValue(-1); });

  value_label = new QLabel();
  value_label->setAlignment(Qt::AlignCenter);
  value_label->setStyleSheet("font-size: 40px; color: #ffffff; min-width: 170px;");

  plus_btn = new QPushButton("+");
  plus_btn->setStyleSheet(btn_style);
  connect(plus_btn, &QPushButton::clicked, [=]() { changeValue(+1); });

  btn_layout->addStretch();
  btn_layout->addWidget(minus_btn);
  btn_layout->addWidget(value_label);
  btn_layout->addWidget(plus_btn);

  qobject_cast<QVBoxLayout*>(layout())->addWidget(btn_widget);
  refresh();
}

void NtuneValueControl::changeValue(int delta) {
  double v = readNtuneValueS(group_, key_, vdefault_);
  v += delta * step_;
  // 부동소수 오차 정리
  double round_scale = std::pow(10.0, decimals_);
  v = std::round(v * round_scale) / round_scale;
  v = std::max(vmin_, std::min(vmax_, v));

  QString pkey; double param_scale;
  if (group_ == "torque" && torqueParamOf(key_, &pkey, &param_scale)) {
    Params().put(pkey.toStdString(), std::to_string((int)std::llround(v * param_scale)));
    Params().put("LateralTorqueCustom", "1");
  } else if (group_ == "torque") {
    writeNtuneTorqueValueS(key_, v);
  } else {
    writeNtuneCommonValueS(key_, v);
  }
  refresh();
}

void NtuneValueControl::refresh() {
  double v = readNtuneValueS(group_, key_, vdefault_);
  if (decimals_ == 0) {
    value_label->setText(v > 0.5 ? "ON" : "OFF");
  } else {
    value_label->setText(QString::number(v, 'f', decimals_));
  }
  minus_btn->setEnabled(v > vmin_ + 1e-9);
  plus_btn->setEnabled(v < vmax_ - 1e-9);
}

// 포팅판 학습 대상 파라미터의 공장 기본값 (python carrot_learning.py 와 동일)
static const std::map<std::string, std::string> kAutoTunerDefaults = {
  {"TFollowGap1", "110"},
  {"TFollowGap2", "120"},
  {"TFollowGap3", "140"},
  {"TFollowGap4", "160"},
  {"TFollowSpeedRatio", "120"},
  {"OffsetT…20789 tokens truncated…mBrakeReleaseDist", "브레이크 해제 앞차거리 (m)",
      "앞차가 있을 때 이 거리 이상에서만 브레이크 해제 오토리줌을 허용합니다.",
      "../assets/offroad/icon_road.png", 2, 50, 1, 0, 10, this));

  list->addItem(new ParamValueControlF(
      "JerkStartLimit", "출발 저크 제한 (×0.1 m/s³)",
      "롱컨 활성 직후 가속·감속 변화 속도를 제한합니다. 값이 작으면 부드럽고, 크면 반응이 빨라집니다.",
      "../assets/offroad/icon_road.png", 5, 30, 1, 0, 10, this));

  list->addItem(new ParamControl(
      "SoftHoldMode", "소프트홀드",
      "브레이크를 밟고 완전히 정지하면 정차 제동을 유지합니다. 앞차나 녹색 신호만으로는 출발하지 않으며 RES/+ 또는 가속페달로 해제합니다.",
      "../assets/offroad/icon_road.png", this));

  list->addItem(new ParamValueControlF(
      "StartAccelApply", "Start Acceleration",
      "정지 후 출발 가속도입니다. 표시값에 0.02m/s²를 곱해 적용합니다.",
      "../assets/offroad/icon_openpilot.png", 0, 50, 1, 0, 25, this));

  list->addItem(new ParamValueControlF(
      "StopAccelApply", "Stop Accel Apply",
      "정지 마무리 제동값입니다. 표시값에 -0.02m/s²를 곱해 적용하며 0은 추가 제동을 끕니다.",
      "../assets/offroad/icon_openpilot.png", 0, 100, 5, 0, 30, this));

  list->addItem(horizontal_line());

  const std::array<std::tuple<const char*, const char*, int>, 6> accel_controls = {{
    {"CruiseMaxAccel0", "Max Accel 0 km/h", 180},
    {"CruiseMaxAccel40", "Max Accel 40 km/h", 117},
    {"CruiseMaxAccel60", "Max Accel 60 km/h", 103},
    {"CruiseMaxAccel80", "Max Accel 80 km/h", 89},
    {"CruiseMaxAccel110", "Max Accel 110 km/h", 74},
    {"CruiseMaxAccel140", "Max Accel 140 km/h", 61},
  }};
  for (const auto& [key, title, default_value] : accel_controls) {
    list->addItem(new ParamValueControlF(
        key, title, "해당 속도 구간의 최대 크루즈 가속도입니다 (×0.01m/s²).",
        "../assets/offroad/icon_openpilot.png", 10, 250, 5, 0, default_value, this));
  }

  list->addItem(horizontal_line());

  const std::array<std::tuple<const char*, const char*, int>, 4> gap_controls = {{
    {"TFollowGap1", "TR Gap 1", 110},
    {"TFollowGap2", "TR Gap 2", 120},
    {"TFollowGap3", "TR Gap 3", 140},
    {"TFollowGap4", "TR Gap 4", 160},
  }};
  for (const auto& [key, title, default_value] : gap_controls) {
    list->addItem(new ParamValueControlF(
        key, title, "해당 크루즈 GAP의 추종시간입니다 (×0.01초).",
        "../assets/offroad/icon_openpilot.png", 70, 300, 5, 0, default_value, this));
  }

  list->addItem(new ParamValueControlF(
      "TFollowSpeedRatio", "TR Speed Ratio",
      "속도에 따라 추종시간을 늘리는 비율입니다 (%).",
      "../assets/offroad/icon_openpilot.png", 100, 300, 5, 0, 120, this));
  list->addItem(new ParamValueControlF(
      "InitialCruiseGap", "Initial Cruise Gap",
      "크루즈가 처음 활성화될 때 선택할 GAP입니다. 0은 차량 기본값을 유지합니다.",
      "../assets/offroad/icon_openpilot.png", 0, 4, 1, 0, 0, this));

  list->addItem(horizontal_line());

  list->addItem(new ParamValueControlF(
      "LongTuningKpV", "Longitudinal Kp", "속도 오차 비례 게인입니다 (×0.01).",
      "../assets/offroad/icon_openpilot.png", 0, 200, 5, 0, 100, this));
  list->addItem(new ParamValueControlF(
      "LongTuningKiV", "Longitudinal Ki", "누적 속도 오차 적분 게인입니다 (×0.001).",
      "../assets/offroad/icon_openpilot.png", 0, 2000, 5, 0, 0, this));
  list->addItem(new ParamValueControlF(
      "LongTuningKf", "Longitudinal Feedforward", "목표 가속도 피드포워드 게인입니다 (×0.01).",
      "../assets/offroad/icon_openpilot.png", 0, 200, 5, 0, 100, this));
  list->addItem(new ParamValueControlF(
      "LongitudinalActuatorDelayLowerBound", "Actuator Delay Lower Bound",
      "종방향 액추에이터의 짧은 지연값입니다 (×0.01초). 0은 차량 기본값을 사용합니다.",
      "../assets/offroad/icon_openpilot.png", 0, 100, 5, 0, 0, this));
  list->addItem(new ParamValueControlF(
      "LongitudinalActuatorDelayUpperBound", "Actuator Delay Upper Bound",
      "종방향 액추에이터의 긴 지연값입니다 (×0.01초). 0은 차량 기본값을 사용합니다.",
      "../assets/offroad/icon_openpilot.png", 0, 100, 5, 0, 0, this));

  list->addItem(horizontal_line());

  list->addItem(new ParamValueControlF(
      "ACCStopDistance", "ACC Stop Distance",
      "ACC 모드에서 앞차 뒤에 정지할 때 유지할 거리입니다 (m).",
      "../assets/offroad/icon_road.png", 1, 10, 1, 0, 6, this));
  list->addItem(new ParamValueControlF(
      "E2EStopDistance", "E2E Stop Distance",
      "모델이 예측한 신호 또는 정지선 앞에서 유지할 거리입니다 (m).",
      "../assets/offroad/icon_road.png", 1, 15, 1, 0, 6, this));

  ScrollView *scroller = new ScrollView(list, this);
  scroller->setVerticalScrollBarPolicy(Qt::ScrollBarAsNeeded);
  layout->addWidget(scroller);
}

/////////////////////////////////////////////////////////////////////////

VIPPanel::VIPPanel(QWidget* parent) : QWidget(parent) {
  QVBoxLayout* layout = new QVBoxLayout(this);
  layout->setContentsMargins(50, 20, 50, 20);
  layout->setSpacing(0);

  ListWidget* list = new ListWidget(this);
  list->setSpacing(0);

  // ── 조향 실시간 튜닝 (nTune 파일 직접 조절) ───────────────────
  list->addItem(horizontal_line());

  list->addItem(new ParamValueControlF("CustomSteerRatio",
      "Steer Ratio",
      "조향비 (×0.01). 크면 같은 곡률에 핸들을 더 많이 돌립니다.\n"
      "Live Steer Ratio 가 켜져 있으면 이 값 대신 학습값이 쓰입니다.\n"
      "범위: 1000 ~ 2000  /  기본값: 1650 (=16.50)",
      "../assets/offroad/icon_openpilot.png", 1000, 2000, 10, 0, 1650, this));

  list->addItem(new ParamControl("UseLiveSteerRatio",
      "Live Steer Ratio",
      "liveParameters 가 학습한 조향비를 사용합니다.\n"
      "끄면 위의 Steer Ratio 고정값을 씁니다.",
      "../assets/offroad/icon_openpilot.png", this));

  list->addItem(new ParamValueControlF("SteerActuatorDelay",
      "Steer Actuator Delay",
      "조향 반응 지연 보상 (×0.01초). 크면 더 미리 조향합니다.\n"
      "커브 인코스/아웃코스 치우침 조정에 씁니다.\n"
      "범위: 0 ~ 80  /  기본값: 10 (=0.10초)",
      "../assets/offroad/icon_openpilot.png", 0, 80, 1, 0, 10, this));

  list->addItem(new ParamControl("LateralTorqueCustom",
      "Torque Custom",
      "켜면 아래 토크 값들이 차량 기본값 대신 사용됩니다.\n"
      "Auto-Tuner 가 값을 쓰면 자동으로 켜집니다.",
      "../assets/offroad/icon_openpilot.png", this));
  
  list->addItem(new NtuneValueControl("torque", "latAccelFactor",
      "Lat Accel Factor",
      "토크 제어 게인입니다. 크면 조향이 강해집니다.\n"
      "범위: 0.50 ~ 4.50  /  기본값: 2.70",
      "../assets/offroad/icon_openpilot.png", 0.5, 4.5, 0.05, 2, 2.7, this));

  list->addItem(new NtuneValueControl("torque", "friction",
      "Friction",
      "정지마찰 보상값입니다. 크면 중앙 부근 응답이 빨라지지만\n"
      "너무 크면 직진에서 좌우로 흔들립니다.\n"
      "범위: 0.000 ~ 0.200  /  기본값: 0.080",
      "../assets/offroad/icon_openpilot.png", 0.0, 0.2, 0.005, 3, 0.08, this));

  list->addItem(new ParamValueControlF("LateralTorqueKpV",
      "Torque Kp", "비례 게인 (×0.01).  기본값: 10",
      "../assets/offroad/icon_openpilot.png", 0, 500, 5, 0, 10, this));

  list->addItem(new ParamValueControlF("LateralTorqueKiV",
      "Torque Ki", "적분 게인 (×0.01).  기본값: 10",
      "../assets/offroad/icon_openpilot.png", 0, 200, 1, 0, 10, this));

  list->addItem(new ParamValueControlF("LateralTorqueKf",
      "Torque Kf", "피드포워드 게인 (×0.01).  기본값: 100",
      "../assets/offroad/icon_openpilot.png", 0, 200, 5, 0, 100, this));

  list->addItem(new ParamValueControlF("LateralTorqueKd",
      "Torque Kd", "미분 게인 (×0.01).  기본값: 0",
      "../assets/offroad/icon_openpilot.png", 0, 200, 5, 0, 0, this));

  list->addItem(new ParamValueControlF("LatAccelFrictionFactor",
      "Friction: Accel Factor",
      "횡가속도 오차를 friction 입력에 반영하는 비율 (×0.01).\n기본값: 70",
      "../assets/offroad/icon_openpilot.png", 0, 300, 5, 0, 70, this));

  list->addItem(new ParamValueControlF("LatJerkFrictionFactor",
      "Friction: Jerk Factor",
      "예측 횡저크를 friction 입력에 반영하는 비율 (×0.01).\n"
      "커브 진입 초기 응답에 영향. 0 이면 사용 안함.  기본값: 40",
      "../assets/offroad/icon_openpilot.png", 0, 200, 5, 0, 40, this));

  list->addItem(horizontal_line());

  // ── Offset Total ─────────────────────────────────────────────
  // 레인모드 + 레인리스 모드 모두 적용. 0.01m 단위, -1.00 ~ +1.00m
  list->addItem(horizontal_line());

  auto *path_offset = new OffsetTotalControl(
      "Offset Total",
      "주행 경로 좌우 통합 보정값입니다. 레인모드·레인리스 모두 적용됩니다.\n"
      "카메라 오프셋은 하드웨어 기본값으로 고정되고 이 값 하나로 조정합니다.\n"
      "왼쪽으로 이동: 양수(+) / 오른쪽으로 이동: 음수(−)\n"
      "범위: −1.00 ~ +1.00m  /  기본값: 0.00m",
      "../assets/offroad/icon_road.png",
      this);
  path_offset->showDescription();
  list->addItem(path_offset);

  list->addItem(horizontal_line());

  // ── Adjust Lane Offset ───────────────────────────────────────
  auto *lane_offset = new AdjustLaneOffsetControl(
      "Adjust Lane Offset",
      "좌우 여유공간이 비대칭일 때 여유 있는 쪽으로 경로를 옮깁니다.\n"
      "좁은 도로에서 대형차 옆을 지날 때 효과가 있습니다.\n"
      "양쪽 다 여유가 있거나 양쪽 다 좁으면 동작하지 않습니다.\n"
      "범위: 0 ~ 40cm (5cm 단위)  /  기본값: OFF",
      "../assets/offroad/icon_road.png",
      this);
  lane_offset->showDescription();
  list->addItem(lane_offset);


  list->addItem(horizontal_line());
  auto *dlp_control = new DynamicLaneProfileControl(
      "Dynamic Lane Profile Mode",
      "Lane only: 항상 차선 기반\n"
      "Lane less: 항상 차선 미사용(e2e)\n"
      "Auto: 차선 인식률에 따라 자동 전환",
      "../assets/offroad/icon_road.png",
      this);
  dlp_control->showDescription();
  list->addItem(dlp_control);

  list->addItem(horizontal_line());

  // ── AutoLaneChangeTimer ──────────────────────────────────────
  auto *lc_timer = new AutoLaneChangeTimerControl(
      "Auto Lane Change Timer",
      "차선변경 자동 시작까지의 대기 시간을 설정합니다.\n"
      "즉시: 조건 충족 즉시 / 0.1s ~ 2.0s: 해당 시간 대기 후 자동 차선변경",
      "../assets/offroad/icon_road.png",
      this);
  lc_timer->showDescription();
  list->addItem(lc_timer);

  // ── AutoLaneChangeSpeed ────────────────────────────────────────
  list->addItem(new ParamValueControlF("AutoLaneChangeSpeed",
      "Auto Lane Change Speed",
      "자동/방향지시등 차선변경이 허용되는 최저 속도입니다 (km/h).\n"
      "이 속도보다 느리면 차선변경이 시작되지 않습니다.",
      "../assets/offroad/icon_road.png", 0, 100, 10, 0, 50, this));

  list->addItem(horizontal_line());

  auto *atc_mode = new ParamValueControlF(
      "CarrotAutoTurnControl", "Carrot Navi ATC Mode",
      "0: Off / 1: steering assist / 2: steering + turn speed / 3: turn speed only. "
      "ATC 모드 선택.",
      "../assets/offroad/icon_road.png", 0, 3, 1, 0, 0, this);
  atc_mode->showDescription();
  list->addItem(atc_mode);

  list->addItem(new ParamValueControlF(
      "CarrotAutoTurnSpeed", "Carrot ATC Turn Speed",
      "회전 구간 근처에서의 목표 속도(km/h). 모드2 또는3일때만 작동",
      "../assets/offroad/icon_speed_limit.png", 30, 60, 5, 0, 30, this));

  list->addItem(new ParamValueControlF(
      "CarrotAutoTurnEndTime", "Carrot ATC Speed Timing",
      "회전 몇 초 전에 목표속도까지 줄여놓을지를 정하는 타이밍값. 모드 2 or 3일때만 작동.",
      "../assets/offroad/icon_road.png", 2, 12, 1, 0, 6, this));

  list->addItem(horizontal_line());

  // ── 기어 변경 팝업 애니메이션 ─────────────────────────────────
  list->addItem(horizontal_line());

  auto *gearAnimToggle = new ParamControl("ShowGearAnimation",
      "기어 팝업 애니메이션",
      "변속단이 바뀔 때 화면 중앙에서 크게 나타났다가 기어 표시 자리로 "
      "날아가는 효과를 켭니다. 주행 중 즉시 반영됩니다.",
      "../assets/offroad/icon_road.png",
      this);
  list->addItem(gearAnimToggle);

  auto *atcAnimToggle = new ParamControl("ShowAtcAnimation",
      "ATC Popup Animation",
      "ATC 상태가 감속, 조향 개입 또는 데이터 끊김으로 전환될 때 화면 중앙에서 "
      "HUD의 ATC 박스로 이동하는 팝업 애니메이션을 표시합니다.",
      "../assets/offroad/icon_road.png",
      this);
  list->addItem(atcAnimToggle);

  // ── 좌측 HUD 박스 표시 ────────────────────────────────────────
  auto *carrotHudToggle = new ParamControl("ShowCarrotHud",
      "좌측 HUD 박스 표시",
      "화면 좌측의 속도·크루즈·GAP·기어·NORM·LIMIT 등 carrot HUD 박스\n"
      "전체를 보이거나 숨깁니다. 주행 중 즉시 반영됩니다.",
      "../assets/offroad/icon_road.png",
      this);
  list->addItem(carrotHudToggle);

  // ── CarrotPilot Auto-Tuner (commit 9dd5e2c port) ─────────────
  list->addItem(horizontal_line());

  auto *learnToggle = new ParamControl("CarrotLearningActive",
      "Auto-Tuner: 주행 기반 학습",
      "운전자 개입(가속/브레이크/조향)을 학습하여 주차(P단) 시 파라미터 조정을 추천합니다.\n"
      "학습 대상: TFollowGap1~4(추종거리) / OffsetTotal(직진 편차) /\n"
      "TurnEnteringDecel·TurnTurningAcc·TurnLeavingAcc(비전 커브 감속)\n"
      "1회 적용 시 변동폭 ±15 제한, 추종거리 최소 0.90초 보장.",
      "../assets/offroad/icon_shell.png",
      this);
  list->addItem(learnToggle);

  list->addItem(horizontal_line());
  auto *autoApplyToggle = new ParamControl("CarrotLearningAutoApply",
      "Auto-Tuner: 추천 자동 적용",
      "활성화 시 주차(P단) 전환 때 팝업 없이 추천값을 자동 적용하고 이력에 기록합니다.\n"
      "비활성화 시 P단 전환 때 선택 적용 팝업이 표시됩니다.",
      "../assets/offroad/icon_shell.png",
      this);
  list->addItem(autoApplyToggle);

  list->addItem(horizontal_line());
  QPushButton* viewHistoryBtn = new QPushButton("View Tuning History");
  viewHistoryBtn->setObjectName("viewHistoryBtn");
  viewHistoryBtn->setStyleSheet(R"(
    QPushButton {
      margin-top: 10px; margin-bottom: 20px; padding: 10px; height: 120px; border-radius: 15px;
      color: #FFFFFF; background-color: #2C2CE2;
      font-size: 50px; font-weight: 400;
    }
    QPushButton:pressed {
      background-color: #2424FF;
    }
  )");
  connect(viewHistoryBtn, &QPushButton::clicked, [=]() {
    AutoTunerHistoryDialog dlg(this);
    dlg.exec();
  });
  list->addItem(viewHistoryBtn);

  // ── Factory Reset 버튼 (commit e06a7dd) ──
  // Params 기반 학습 대상(TFollowGap/OffsetTotal/Turn*)만 공장 기본값 복원 +
  // 학습 데이터/이력 삭제. nTune 조향값(latAccelFactor/friction/steerActuatorDelay)은
  // 차량별 기준값이라 의도적으로 제외 (사용자 nTune 세팅 보호).
  QPushButton* factoryResetBtn = new QPushButton("Auto-Tuner: Factory Reset");
  factoryResetBtn->setObjectName("factoryResetBtn");
  factoryResetBtn->setStyleSheet(R"(
    QPushButton {
      margin-top: 10px; margin-bottom: 20px; padding: 10px; height: 120px; border-radius: 15px;
      color: #FFFFFF; background-color: #8a1d1d;
      font-size: 50px; font-weight: 400;
    }
    QPushButton:pressed {
      background-color: #B02525;
    }
  )");
  connect(factoryResetBtn, &QPushButton::clicked, [=]() {
    if (ConfirmationDialog::confirm("학습 파라미터(가속/추종거리/직진보정/커브감속)를 모두 공장 기본값으로 되돌리고 학습 데이터·이력을 삭제하시겠습니까?\n\n(조향 nTune 값은 변경되지 않습니다)", this)) {
      Params p;
      for (const auto& [key, val] : kAutoTunerDefaults) {
        p.put(key, val);
      }
      p.remove("CarrotLearningHistory");
      p.remove("CarrotLearningRecommend");
      p.putBool("CarrotLearningPopupReady", false);
      p.putBool("CarrotLearningClear", true);       // 누적 학습 데이터 삭제 (python 처리)
      p.putBool("CarrotTunerFactoryReset", true);   // onroad 인스턴스 재동기화 신호
      ConfirmationDialog::alert("공장 기본값으로 초기화되었습니다.", this);
    }
  });
  list->addItem(factoryResetBtn);

  // 학습 비활성 시 이력/초기화 버튼 숨김 (원본 커밋의 동적 표시 로직)
  bool learn_on = Params().getBool("CarrotLearningActive");
  viewHistoryBtn->setVisible(learn_on);
  factoryResetBtn->setVisible(learn_on);
  connect(learnToggle, &ToggleControl::toggleFlipped, [=](bool state) {
    viewHistoryBtn->setVisible(state);
    factoryResetBtn->setVisible(state);
  });

  ScrollView *scroller = new ScrollView(list, this);
  scroller->setVerticalScrollBarPolicy(Qt::ScrollBarAsNeeded);
  layout->addWidget(scroller);
}

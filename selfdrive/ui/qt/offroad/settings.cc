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
#include <QJsonDocument>
#include <QJsonObject>
#include <QDateTime>
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

// ── nTune steering parameter helpers ───────────────────────────────────
// Read and write the manual nTune steering settings.
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
  int v = cur.empty() ? vdefault_ : std::atoi(cur.c_str());
  v = std::max(vmin_, std::min(vmax_, v + delta * step_));
  params.put(param_.toStdString(), std::to_string(v));
  refresh();
}

void ParamValueControlF::refresh() {
  std::string cur = params.get(param_.toStdString());
  int v = cur.empty() ? vdefault_ : std::atoi(cur.c_str());
  v = std::max(vmin_, std::min(vmax_, v));
  if (param_ == "TrafficStopMode") {
    static const QStringList modes = {"ACC", "AUTO", "APILOT"};
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

TogglesPanel::TogglesPanel(SettingsWindow *parent) : ListWidget(parent) {
  std::vector<std::tuple<QString, QString, QString, QString>> toggle_defs{
    {
      "OpenpilotEnabledToggle",
      "Enable openpilot",
      "Use the openpilot system for adaptive cruise control and lane keep driver assistance. Your attention is required at all times to use this feature. Changing this setting takes effect when the car is powered off.",
      "../assets/offroad/icon_openpilot.png",
    },
    {
      "IsLdwEnabled",
      "Enable Lane Departure Warnings",
      "Receive alerts to steer back into the lane when your vehicle drifts over a detected lane line without a turn signal activated while driving over 31 mph (50 km/h).",
      "../assets/offroad/icon_warning.png",
    },
    {
      "IsRHD",
      "Enable Right-Hand Drive",
      "Allow openpilot to obey left-hand traffic conventions and perform driver monitoring on right driver seat.",
      "../assets/offroad/icon_openpilot_mirrored.png",
    },
    {
      "IsMetric",
      "Use Metric System",
      "Display speed in km/h instead of mph.",
      "../assets/offroad/icon_metric.png",
    },
    {
      "RecordFront",
      "Record and Upload Driver Camera",
      "Upload data from the driver facing camera and help improve the driver monitoring algorithm.",
      "../assets/offroad/icon_monitoring.png",
    },
    {
      "ExperimentalMode",
      "Experimental mode",
      "알파 수준 기능을 활성화합니다. (실험 기능/안전 경고를 확인하세요)",
      "../assets/img_experimental_white.svg",
    },
    {
      "ExperimentalLongitudinalEnabled",
      "Experimental openpilot longitudinal control",
      "<b>WARNING: openpilot longitudinal control is experimental for this car and will disable AEB.</b><br>\
          openpilot defaults to the car's built-in ACC instead of openpilot's longitudinal control on this car. Enable this to switch to openpilot longitudinal control.",
      "../assets/offroad/icon_speed_limit.png",
    },
#ifdef ENABLE_MAPS
    {
      "NavSettingTime24h",
      "Show ETA in 24h format",
      "Use 24h format instead of am/pm",
      "../assets/offroad/icon_metric.png",
    },
#endif
  };

  for (auto &[param, title, desc, icon] : toggle_defs) {
    auto toggle = new ParamControl(param, title, desc, icon, this);
    bool locked = params.getBool((param + "Lock").toStdString());
    toggle->setEnabled(!locked);
    addItem(toggle);
    toggles[param.toStdString()] = toggle;
  }

  toggles["ExperimentalMode"]->setActiveIcon("../assets/img_experimental.svg");
  toggles["ExperimentalMode"]->setConfirmation(true, true);
  toggles["ExperimentalLongitudinalEnabled"]->setConfirmation(true, false);

  connect(toggles["ExperimentalLongitudinalEnabled"], &ToggleControl::toggleFlipped, [=]() {
    updateToggles();
  });
}

void TogglesPanel::expandToggleDescription(const QString &param) {
  toggles[param.toStdString()]->showDescription();
}

void TogglesPanel::showEvent(QShowEvent *event) {
  updateToggles();
}

void TogglesPanel::updateToggles() {
  auto e2e_toggle = toggles["ExperimentalMode"];
  auto op_long_toggle = toggles["ExperimentalLongitudinalEnabled"];
  const QString e2e_description = tr("\
    openpilot defaults to driving in <b>chill mode</b>.\
    Experimental mode enables <b>alpha-level features</b> that aren't ready for chill mode. \
    Experimental features are listed below:\
    <br> \
    <h4>🌮 End-to-End Longitudinal Control 🌮</h4> \
    Let the driving model control the gas and brakes. openpilot will drive as it thinks a human would, including stopping for red lights and stop signs.");

  auto cp_bytes = params.get("CarParamsPersistent");
  if (!cp_bytes.empty()) {
    AlignedBuffer aligned_buf;
    capnp::FlatArrayMessageReader cmsg(aligned_buf.align(cp_bytes.data(), cp_bytes.size()));
    cereal::CarParams::Reader CP = cmsg.getRoot<cereal::CarParams>();

    if (!CP.getExperimentalLongitudinalAvailable()) {
      params.remove("ExperimentalLongitudinalEnabled");
    }
    op_long_toggle->setVisible(CP.getExperimentalLongitudinalAvailable());

    const bool op_long = CP.getOpenpilotLongitudinalControl() && !CP.getExperimentalLongitudinalAvailable();
    const bool exp_long_enabled = CP.getExperimentalLongitudinalAvailable() && params.getBool("ExperimentalLongitudinalEnabled");
    if (op_long || exp_long_enabled) {
      e2e_toggle->setEnabled(true);
      e2e_toggle->setDescription(e2e_description);
    } else {
      e2e_toggle->setEnabled(false);
      params.remove("ExperimentalMode");
      const QString no_long = "openpilot longitudinal control is not currently available for this car.";
      const QString exp_long = "Enable experimental longitudinal control to enable this.";
      e2e_toggle->setDescription("<b>" + (CP.getExperimentalLongitudinalAvailable() ? exp_long : no_long) + "</b><br><br>" + e2e_description);
    }
    e2e_toggle->refresh();
  } else {
    e2e_toggle->setDescription(e2e_description);
    op_long_toggle->setVisible(true);
  }
}

DevicePanel::DevicePanel(SettingsWindow *parent) : ListWidget(parent) {
  setSpacing(50);
  addItem(new LabelControl("Dongle ID", getDongleId().value_or("N/A")));
  addItem(new LabelControl("Serial", params.get("HardwareSerial").c_str()));

  QHBoxLayout *reset_layout = new QHBoxLayout();
  reset_layout->setSpacing(30);

  QPushButton *restart_openpilot_btn = new QPushButton("Soft restart");
  restart_openpilot_btn->setStyleSheet("height: 120px;border-radius: 15px;background-color: #393939;");
  reset_layout->addWidget(restart_openpilot_btn);
  QObject::connect(restart_openpilot_btn, &QPushButton::released, [=]() {
    emit closeSettings();
    QTimer::singleShot(1000, []() {
      Params().putBool("SoftRestartTriggered", true);
    });
  });

  QPushButton *reset_calib_btn = new QPushButton("Reset Calibration");
  reset_calib_btn->setStyleSheet("height: 120px;border-radius: 15px;background-color: #393939;");
  reset_layout->addWidget(reset_calib_btn);
  QObject::connect(reset_calib_btn, &QPushButton::released, [=]() {
    if (ConfirmationDialog::confirm("Are you sure you want to reset calibration and live params?", this)) {
      Params().remove("CalibrationParams");
      Params().remove("LiveParameters");
      emit closeSettings();
      QTimer::singleShot(1000, []() {
        Params().putBool("SoftRestartTriggered", true);
      });
    }
  });

  addItem(reset_layout);

  auto dcamBtn = new ButtonControl("Driver Camera", "PREVIEW",
                                   "Preview the driver facing camera to help optimize device mounting position for best driver monitoring experience. (vehicle must be off)");
  connect(dcamBtn, &ButtonControl::clicked, [=]() { emit showDriverView(); });
  addItem(dcamBtn);

  auto resetCalibBtn = new ButtonControl("Reset Calibration", "RESET", " ");
  connect(resetCalibBtn, &ButtonControl::showDescription, this, &DevicePanel::updateCalibDescription);
  connect(resetCalibBtn, &ButtonControl::clicked, [&]() {
    if (ConfirmationDialog::confirm("Are you sure you want to reset calibration?", this)) {
      params.remove("CalibrationParams");
    }
  });
  addItem(resetCalibBtn);

  if (!params.getBool("Passive")) {
    auto retrainingBtn = new ButtonControl("Review Training Guide", "REVIEW", "Review the rules, features, and limitations of openpilot");
    connect(retrainingBtn, &ButtonControl::clicked, [=]() {
      if (ConfirmationDialog::confirm("Are you sure you want to review the training guide?", this)) {
        emit reviewTrainingGuide();
      }
    });
    addItem(retrainingBtn);
  }

  if (Hardware::TICI()) {
    auto regulatoryBtn = new ButtonControl("Regulatory", "VIEW", "");
    connect(regulatoryBtn, &ButtonControl::clicked, [=]() {
      const std::string txt = util::read_file("../assets/offroad/fcc.html");
      ConfirmationDialog::rich(QString::fromStdString(txt), this);
    });
    addItem(regulatoryBtn);
  }

  QHBoxLayout *power_layout = new QHBoxLayout();
  power_layout->setSpacing(30);

  QPushButton *rebuild_btn = new QPushButton("Rebuild");
  rebuild_btn->setObjectName("rebuild_btn");
  power_layout->addWidget(rebuild_btn);
  QObject::connect(rebuild_btn, &QPushButton::clicked, [=]() {
    if (ConfirmationDialog::confirm("Are you sure you want to rebuild?", this)) {
      std::system("cd /data/openpilot && scons -c");
      std::system("rm /data/openpilot/.sconsign.dblite");
      std::system("rm /data/openpilot/prebuilt");
      std::system("rm -rf /tmp/scons_cache");
      if (Hardware::TICI())
        std::system("sudo reboot");
      else
        std::system("reboot");
    }
  });

  QPushButton *reboot_btn = new QPushButton("Reboot");
  reboot_btn->setObjectName("reboot_btn");
  power_layout->addWidget(reboot_btn);
  QObject::connect(reboot_btn, &QPushButton::clicked, this, &DevicePanel::reboot);

  QPushButton *poweroff_btn = new QPushButton("Power Off");
  poweroff_btn->setObjectName("poweroff_btn");
  power_layout->addWidget(poweroff_btn);
  QObject::connect(poweroff_btn, &QPushButton::clicked, this, &DevicePanel::poweroff);

  if (Hardware::TICI()) {
    connect(uiState(), &UIState::offroadTransition, poweroff_btn, &QPushButton::setVisible);
  }

  setStyleSheet(R"(
    #reboot_btn { height: 120px; border-radius: 15px; background-color: #393939; }
    #reboot_btn:pressed { background-color: #4a4a4a; }
    #rebuild_btn { height: 120px; border-radius: 15px; background-color: #393939; }
    #rebuild_btn:pressed { background-color: #4a4a4a; }
    #poweroff_btn { height: 120px; border-radius: 15px; background-color: #E22C2C; }
    #poweroff_btn:pressed { background-color: #FF2424; }
  )");
  addItem(power_layout);
}

void DevicePanel::updateCalibDescription() {
  QString desc =
      "openpilot requires the device to be mounted within 4° left or right and "
      "within 5° up or 8° down. openpilot is continuously calibrating, resetting is rarely required.";
  std::string calib_bytes = Params().get("CalibrationParams");
  if (!calib_bytes.empty()) {
    try {
      AlignedBuffer aligned_buf;
      capnp::FlatArrayMessageReader cmsg(aligned_buf.align(calib_bytes.data(), calib_bytes.size()));
      auto calib = cmsg.getRoot<cereal::Event>().getLiveCalibration();
      if (calib.getCalStatus() != 0) {
        double pitch = calib.getRpyCalib()[1] * (180 / M_PI);
        double yaw = calib.getRpyCalib()[2] * (180 / M_PI);
        desc += QString(" Your device is pointed %1° %2 and %3° %4.")
                    .arg(QString::number(std::abs(pitch), 'g', 1), pitch > 0 ? "down" : "up",
                         QString::number(std::abs(yaw), 'g', 1), yaw > 0 ? "left" : "right");
      }
    } catch (kj::Exception) {
      qInfo() << "invalid CalibrationParams";
    }
  }
  qobject_cast<ButtonControl *>(sender())->setDescription(desc);
}

void DevicePanel::reboot() {
  if (!uiState()->engaged()) {
    if (ConfirmationDialog::confirm("Are you sure you want to reboot?", this)) {
      if (!uiState()->engaged()) {
        Params().putBool("DoReboot", true);
      }
    }
  } else {
    ConfirmationDialog::alert("Disengage to Reboot", this);
  }
}

void DevicePanel::poweroff() {
  if (!uiState()->engaged()) {
    if (ConfirmationDialog::confirm("Are you sure you want to power off?", this)) {
      if (!uiState()->engaged()) {
        Params().putBool("DoShutdown", true);
      }
    }
  } else {
    ConfirmationDialog::alert("Disengage to Power Off", this);
  }
}

SoftwarePanel::SoftwarePanel(QWidget* parent) : ListWidget(parent) {
  gitBranchLbl = new LabelControl("Git Branch");
  gitCommitLbl = new LabelControl("Git Commit");
  osVersionLbl = new LabelControl("OS Version");
  versionLbl = new LabelControl("Version", "", QString::fromStdString(params.get("ReleaseNotes")).trimmed());
  lastUpdateLbl = new LabelControl("Last Update Check", "", "The last time openpilot successfully checked for an update. The updater only runs while the car is off.");
  updateBtn = new ButtonControl("Check for Update", "");
  connect(updateBtn, &ButtonControl::clicked, [=]() {
    if (params.getBool("IsOffroad")) {
      fs_watch->addPath(QString::fromStdString(params.getParamPath("LastUpdateTime")));
      fs_watch->addPath(QString::fromStdString(params.getParamPath("UpdateFailedCount")));
      updateBtn->setText("CHECKING");
      updateBtn->setEnabled(false);
    }
    std::system("pkill -1 -f selfdrive.updated");
  });

  auto uninstallBtn = new ButtonControl("Uninstall " + getBrand(), "UNINSTALL");
  connect(uninstallBtn, &ButtonControl::clicked, [&]() {
    if (ConfirmationDialog::confirm("Are you sure you want to uninstall?", this)) {
      params.putBool("DoUninstall", true);
    }
  });
  connect(uiState(), &UIState::offroadTransition, uninstallBtn, &QPushButton::setEnabled);

  QWidget *widgets[] = {versionLbl, lastUpdateLbl, updateBtn, gitBranchLbl, gitCommitLbl, osVersionLbl, uninstallBtn};
  for (QWidget* w : widgets) {
    addItem(w);
  }

  fs_watch = new QFileSystemWatcher(this);
  QObject::connect(fs_watch, &QFileSystemWatcher::fileChanged, [=](const QString path) {
    if (path.contains("UpdateFailedCount") && std::atoi(params.get("UpdateFailedCount").c_str()) > 0) {
      lastUpdateLbl->setText("failed to fetch update");
      updateBtn->setText("CHECK");
      updateBtn->setEnabled(true);
    } else if (path.contains("LastUpdateTime")) {
      updateLabels();
    }
  });
}

void SoftwarePanel::showEvent(QShowEvent *event) {
  updateLabels();
}

void SoftwarePanel::updateLabels() {
  QString lastUpdate = "";
  auto tm = params.get("LastUpdateTime");
  if (!tm.empty()) {
    lastUpdate = timeAgo(QDateTime::fromString(QString::fromStdString(tm + "Z"), Qt::ISODate));
  }

  versionLbl->setText(getBrandVersion());
  lastUpdateLbl->setText(lastUpdate);
  updateBtn->setText("CHECK");
  updateBtn->setEnabled(true);
  gitBranchLbl->setText(QString::fromStdString(params.get("GitBranch")));
  gitCommitLbl->setText(QString::fromStdString(params.get("GitCommit")).left(10));
  osVersionLbl->setText(QString::fromStdString(Hardware::get_os_version()).trimmed());
}

C2NetworkPanel::C2NetworkPanel(QWidget *parent) : QWidget(parent) {
  QVBoxLayout *layout = new QVBoxLayout(this);
  layout->setContentsMargins(50, 0, 50, 0);

  ListWidget *list = new ListWidget();
  list->setSpacing(30);
#ifdef QCOM
  auto wifiBtn = new ButtonControl("Wi-Fi Settings", "OPEN");
  QObject::connect(wifiBtn, &ButtonControl::clicked, [=]() { HardwareEon::launch_wifi(); });
  list->addItem(wifiBtn);

  auto tetheringBtn = new ButtonControl("Tethering Settings", "OPEN");
  QObject::connect(tetheringBtn, &ButtonControl::clicked, [=]() { HardwareEon::launch_tethering(); });
  list->addItem(tetheringBtn);
#endif
  ipaddress = new LabelControl("IP Address", "");
  list->addItem(ipaddress);

  list->addItem(new SshToggle());
  list->addItem(new SshControl());
  layout->addWidget(list);
  layout->addStretch(1);
}

void C2NetworkPanel::showEvent(QShowEvent *event) {
  ipaddress->setText(getIPAddress());
}

QString C2NetworkPanel::getIPAddress() {
  std::string result = util::check_output("ifconfig wlan0");
  if (result.empty()) return "";

  const std::string inetaddrr = "inet addr:";
  std::string::size_type begin = result.find(inetaddrr);
  if (begin == std::string::npos) return "";

  begin += inetaddrr.length();
  std::string::size_type end = result.find(' ', begin);
  if (end == std::string::npos) return "";

  return result.substr(begin, end - begin).c_str();
}

QWidget *network_panel(QWidget *parent) {
#ifdef QCOM
  return new C2NetworkPanel(parent);
#else
  return new Networking(parent);
#endif
}

static QStringList get_list(const char* path)
{
  QStringList stringList;
  QFile textFile(path);
  if(textFile.open(QIODevice::ReadOnly))
  {
      QTextStream textStream(&textFile);
      while (true)
      {
        QString line = textStream.readLine();
        if (line.isNull())
            break;
        else
            stringList.append(line);
      }
  }
  return stringList;
}

void SettingsWindow::showEvent(QShowEvent *event) {
  setCurrentPanel(0);
}

void SettingsWindow::setCurrentPanel(int index, const QString &param) {
  panel_widget->setCurrentIndex(index);
  nav_btns->buttons()[index]->setChecked(true);
  if (!param.isEmpty()) {
    emit expandToggleDescription(param);
  }
}

SettingsWindow::SettingsWindow(QWidget *parent) : QFrame(parent) {

  sidebar_widget = new QWidget;
  QVBoxLayout *sidebar_layout = new QVBoxLayout(sidebar_widget);
  sidebar_layout->setMargin(0);
  panel_widget = new QStackedWidget();
  panel_widget->setStyleSheet(R"(
    border-radius: 30px;
    background-color: #292929;
  )");

  QPushButton *close_btn = new QPushButton("← Back");
  close_btn->setStyleSheet(R"(
    QPushButton {
      font-size: 50px;
      font-weight: bold;
      margin: 0px;
      padding: 15px;
      border-width: 0;
      border-radius: 30px;
      color: #dddddd;
      background-color: #444444;
    }
    QPushButton:pressed {
      background-color: #3B3B3B;
    }
  )");
  close_btn->setFixedSize(300, 110);
  sidebar_layout->addSpacing(10);
  sidebar_layout->addWidget(close_btn, 0, Qt::AlignRight);
  sidebar_layout->addSpacing(10);
  QObject::connect(close_btn, &QPushButton::clicked, this, &SettingsWindow::closeSettings);

  DevicePanel *device = new DevicePanel(this);
  QObject::connect(device, &DevicePanel::reviewTrainingGuide, this, &SettingsWindow::reviewTrainingGuide);
  QObject::connect(device, &DevicePanel::showDriverView, this, &SettingsWindow::showDriverView);
  QObject::connect(device, &DevicePanel::closeSettings, this, &SettingsWindow::closeSettings);

  TogglesPanel *toggles = new TogglesPanel(this);
  QObject::connect(this, &SettingsWindow::expandToggleDescription, toggles, &TogglesPanel::expandToggleDescription);

  QList<QPair<QString, QWidget *>> panels = {
    {"Device", device},
    {"Network", network_panel(this)},
    {"Toggles", toggles},
    {"Software", new SoftwarePanel(this)},
    {"Community", new CommunityPanel(this)},
    {"UI 설정", new UISettingsPanel(this)},
    {"조향", new VIPPanel(this)},
    {"Cruise", new CruisePanel(this)},
    {"롱컨", new LongitudinalPanel(this)},
  };

#ifdef ENABLE_MAPS
  auto map_panel = new MapPanel(this);
  panels.push_back({"Navigation", map_panel});
  QObject::connect(map_panel, &MapPanel::closeSettings, this, &SettingsWindow::closeSettings);
#endif

  const int padding = panels.size() > 7 ? 10 : (panels.size() > 3 ? 25 : 35);

  nav_btns = new QButtonGroup(this);
  for (auto &[name, panel] : panels) {
    QPushButton *btn = new QPushButton(name);
    btn->setCheckable(true);
    btn->setChecked(nav_btns->buttons().size() == 0);
    btn->setStyleSheet(QString(R"(
      QPushButton {
        color: grey;
        border: none;
        background: none;
        font-size: 60px;
        font-weight: 500;
        padding-top: %1px;
        padding-bottom: %1px;
      }
      QPushButton:checked {
        color: white;
      }
      QPushButton:pressed {
        color: #ADADAD;
      }
    )").arg(padding));

    nav_btns->addButton(btn);
    sidebar_layout->addWidget(btn, 0, Qt::AlignRight);

    const int lr_margin = name != "Network" ? 50 : 0;
    panel->setContentsMargins(lr_margin, 25, lr_margin, 25);

    ScrollView *panel_frame = new ScrollView(panel, this);
    panel_widget->addWidget(panel_frame);

    QObject::connect(btn, &QPushButton::clicked, [=, w = panel_frame]() {
      btn->setChecked(true);
      panel_widget->setCurrentWidget(w);
    });
  }
  sidebar_layout->setContentsMargins(50, 50, 100, 50);

  QHBoxLayout *main_layout = new QHBoxLayout(this);

  sidebar_widget->setFixedWidth(500);
  main_layout->addWidget(sidebar_widget);
  main_layout->addWidget(panel_widget);

  setStyleSheet(R"(
    * {
      color: white;
      font-size: 50px;
    }
    SettingsWindow {
      background-color: black;
    }
  )");
}

void SettingsWindow::hideEvent(QHideEvent *event) {
#ifdef QCOM
  HardwareEon::close_activities();
#endif
}


/////////////////////////////////////////////////////////////////////////

CommunityPanel::CommunityPanel(QWidget* parent) : QWidget(parent) {

  main_layout = new QStackedLayout(this);

  homeScreen = new QWidget(this);
  QVBoxLayout* vlayout = new QVBoxLayout(homeScreen);
  vlayout->setContentsMargins(0, 20, 0, 20);

  QString selected = QString::fromStdString(Params().get("SelectedCar"));

  QPushButton* selectCarBtn = new QPushButton(selected.length() ? selected : "Select your car");
  selectCarBtn->setObjectName("selectCarBtn");
  connect(selectCarBtn, &QPushButton::clicked, [=]() { main_layout->setCurrentWidget(selectCar); });

  homeWidget = new QWidget(this);
  QVBoxLayout* toggleLayout = new QVBoxLayout(homeWidget);
  homeWidget->setObjectName("homeWidget");

  ScrollView *scroller = new ScrollView(homeWidget, this);
  scroller->setVerticalScrollBarPolicy(Qt::ScrollBarAsNeeded);

  main_layout->addWidget(homeScreen);

  selectCar = new SelectCar(this);
  connect(selectCar, &SelectCar::backPress, [=]() { main_layout->setCurrentWidget(homeScreen); });
  connect(selectCar, &SelectCar::selectedCar, [=]() {
     QString selected = QString::fromStdString(Params().get("SelectedCar"));
     selectCarBtn->setText(selected.length() ? selected : "Select your car");
     main_layout->setCurrentWidget(homeScreen);
  });
  main_layout->addWidget(selectCar);

  QString lateral_control = QString::fromStdString(Params().get("LateralControl"));
  if(lateral_control.length() == 0)
    lateral_control = "TORQUE";

  QPushButton* lateralControlBtn = new QPushButton(lateral_control);
  lateralControlBtn->setObjectName("lateralControlBtn");
  connect(lateralControlBtn, &QPushButton::clicked, [=]() { main_layout->setCurrentWidget(lateralControl); });

  lateralControl = new LateralControl(this);
  connect(lateralControl, &LateralControl::backPress, [=]() { main_layout->setCurrentWidget(homeScreen); });
  connect(lateralControl, &LateralControl::selected, [=]() {
     QString lateral_control = QString::fromStdString(Params().get("LateralControl"));
     if(lateral_control.length() == 0)
       lateral_control = "TORQUE";
     lateralControlBtn->setText(lateral_control);
     main_layout->setCurrentWidget(homeScreen);
  });
  main_layout->addWidget(lateralControl);

  QHBoxLayout* layoutBtn = new QHBoxLayout(homeWidget);
  layoutBtn->addWidget(lateralControlBtn);
  layoutBtn->addSpacing(10);
  layoutBtn->addWidget(selectCarBtn);

  vlayout->addSpacing(10);
  vlayout->addLayout(layoutBtn, 0);
  vlayout->addSpacing(10);
  vlayout->addWidget(scroller, 1);

  QPalette pal = palette();
  pal.setColor(QPalette::Background, QColor(0x29, 0x29, 0x29));
  setAutoFillBackground(true);
  setPalette(pal);

  setStyleSheet(R"(
    #back_btn, #selectCarBtn, #lateralControlBtn {
      font-size: 50px;
      margin: 0px;
      padding: 20px;
      border-width: 0;
      border-radius: 30px;
      color: #dddddd;
      background-color: #444444;
    }
  )");

  toggleLayout->addWidget(new TimeZoneSelectCombo());
  toggleLayout->addWidget(horizontal_line());

  QList<ParamControl*> toggles;
  toggles.append(new ParamControl("UseClusterSpeed",
                                            "Use Cluster Speed",
                                            "Use cluster speed instead of wheel speed.",
                                            "../assets/offroad/icon_road.png",
                                            this));
  toggles.append(new ParamControl("LongControlEnabled",
                                            "Enable HKG Long Control",
                                            "warnings: it is beta, be careful!! Openpilot will control the speed of your car",
                                            "../assets/offroad/icon_road.png",
                                            this));
  toggles.append(new ParamControl("IsLdwsCar",
                                            "LDWS",
                                            "If your car only supports LDWS, turn it on.",
                                            "../assets/offroad/icon_openpilot.png",
                                            this));
  toggles.append(new ParamControl("LaneChangeEnabled",
                                            "Enable Lane Change Assist",
                                            "Perform assisted lane changes with openpilot by checking your surroundings for safety, activating the turn signal and gently nudging the steering wheel towards your desired lane. openpilot is not capable of checking if a lane change is safe. You must continuously observe your surroundings to use this feature.",
                                            "../assets/offroad/icon_road.png",
                                            this));
  toggles.append(new ParamControl("AutoLaneChangeEnabled",
                                            "Enable Auto Lane Change(Nudgeless)",
                                            "warnings: it is beta, be careful!!",
                                            "../assets/offroad/icon_road.png",
                                            this));

  for(ParamControl *toggle : toggles) {
    if(main_layout->count() != 0) {
      toggleLayout->addWidget(horizontal_line());
    }
    toggleLayout->addWidget(toggle);
  }

  toggleLayout->addWidget(horizontal_line());
  toggleLayout->addWidget(new ParamControl("TurnVisionControl",
                                           "최신 비전·지도 커브감속",
                                           "모델 예측 횡가속과 티맵 전방 경로 곡률을 함께 계산해 낮은 목표속도를 적용합니다.",
                                            "../assets/offroad/icon_road.png",
                                            this));
  toggleLayout->addWidget(new ParamValueControlF(
      "AutoCurveSpeedFactor", "비전 커브 감속비율",
      "값이 높을수록 모델 커브를 크게 판단해 더 감속합니다.",
      "../assets/offroad/icon_road.png", 50, 300, 5, 0, 120, this));
  toggleLayout->addWidget(new ParamValueControlF(
      "AutoCurveSpeedLowerLimit", "커브 최저속도",
      "비전 및 티맵 일반도로 커브 목표속도의 최저값(km/h).",
      "../assets/offroad/icon_speed_limit.png", 5, 80, 5, 0, 30, this));
  toggleLayout->addWidget(new ParamValueControlF(
      "MapTurnSpeedFactor", "티맵 지도 커브속도 비율",
      "티맵 전방 경로에서 계산한 일반도로 커브속도 반영비율.",
      "../assets/offroad/icon_road.png", 50, 150, 5, 0, 90, this));
  toggleLayout->addWidget(new ParamValueControlF(
      "AutoNaviSpeedDecelRate", "지도 커브 감속도",
      "티맵 일반도로 커브 진입 감속도(x100 m/s²).",
      "../assets/offroad/icon_road.png", 10, 300, 10, 0, 120, this));
  toggleLayout->addWidget(horizontal_line());
  toggleLayout->addWidget(new ParamControl("SccSmootherSyncGasPressed",
                                            "Sync set speed on gas pressed",
                                            "",
                                            "../assets/offroad/icon_road.png",
                                            this));
  toggleLayout->addWidget(horizontal_line());
  toggleLayout->addWidget(new ParamControl("StockNaviDecelEnabled",
                                            "Stock Navi based deceleration",
                                            "Use the stock navi based deceleration for longcontrol",
                                            "../assets/offroad/icon_road.png",
                                            this));
  toggleLayout->addWidget(horizontal_line());
  toggleLayout->addWidget(new ParamControl("KeepSteeringTurnSignals",
                                            "Keep steering while turn signals",
                                            "",
                                            "../assets/offroad/icon_openpilot.png",
                                            this));
  toggleLayout->addWidget(horizontal_line());
  toggleLayout->addWidget(new ParamControl("HapticFeedbackWhenSpeedCamera",
                                            "Haptic feedback (speed-cam alert)",
                                            "Haptic feedback when a speed camera is detected",
                                            "../assets/offroad/icon_openpilot.png",
                                            this));
  toggleLayout->addWidget(horizontal_line());
  toggleLayout->addWidget(new ParamControl("DisableOpFcw",
                                            "Disable Openpilot FCW",
                                            "",
                                            "../assets/offroad/icon_shell.png",
                                            this));
}

SelectCar::SelectCar(QWidget* parent): QWidget(parent) {

  QVBoxLayout* main_layout = new QVBoxLayout(this);
  main_layout->setMargin(20);
  main_layout->setSpacing(20);

  QPushButton* back = new QPushButton("Back");
  back->setObjectName("back_btn");
  back->setFixedSize(500, 100);
  connect(back, &QPushButton::clicked, [=]() { emit backPress(); });
  main_layout->addWidget(back, 0, Qt::AlignLeft);

  QListWidget* list = new QListWidget(this);
  list->setStyleSheet("QListView {padding: 40px; background-color: #393939; border-radius: 15px; height: 140px;} QListView::item{height: 100px}");
  QScroller::grabGesture(list->viewport(), QScroller::LeftMouseButtonGesture);
  list->setVerticalScrollMode(QAbstractItemView::ScrollPerPixel);

  list->addItem("[ Not selected ]");

  QStringList items = get_list("/data/params/d/SupportedCars");
  list->addItems(items);
  list->setCurrentRow(0);

  QString selected = QString::fromStdString(Params().get("SelectedCar"));

  int index = 0;
  for(QString item : items) {
    if(selected == item) {
        list->setCurrentRow(index + 1);
        break;
    }
    index++;
  }

  QObject::connect(list, QOverload<QListWidgetItem*>::of(&QListWidget::itemClicked),
    [=](QListWidgetItem* item){
    if(list->currentRow() == 0)
        Params().remove("SelectedCar");
    else
        Params().put("SelectedCar", list->currentItem()->text().toStdString());
    emit selectedCar();
    });

  main_layout->addWidget(list);
}

LateralControl::LateralControl(QWidget* parent): QWidget(parent) {

  QVBoxLayout* main_layout = new QVBoxLayout(this);
  main_layout->setMargin(20);
  main_layout->setSpacing(20);

  QPushButton* back = new QPushButton("Back");
  back->setObjectName("back_btn");
  back->setFixedSize(500, 100);
  connect(back, &QPushButton::clicked, [=]() { emit backPress(); });
  main_layout->addWidget(back, 0, Qt::AlignLeft);

  QListWidget* list = new QListWidget(this);
  list->setStyleSheet("QListView {padding: 40px; background-color: #393939; border-radius: 15px; height: 140px;} QListView::item{height: 100px}");
  QScroller::grabGesture(list->viewport(), QScroller::LeftMouseButtonGesture);
  list->setVerticalScrollMode(QAbstractItemView::ScrollPerPixel);

  QStringList items = {"TORQUE", "LQR", "INDI"};
  list->addItems(items);
  list->setCurrentRow(0);

  QString selectedControl = QString::fromStdString(Params().get("LateralControl"));

  int index = 0;
  for(QString item : items) {
    if(selectedControl == item) {
        list->setCurrentRow(index);
        break;
    }
    index++;
  }

  QObject::connect(list, QOverload<QListWidgetItem*>::of(&QListWidget::itemClicked),
    [=](QListWidgetItem* item){
    Params().put("LateralControl", list->currentItem()->text().toStdString());
    emit selected();
    QTimer::singleShot(1000, []() {
        Params().putBool("SoftRestartTriggered", true);
      });
    });

  main_layout->addWidget(list);
}

/////////////////////////////////////////////////////////////////////////

CruisePanel::CruisePanel(QWidget* parent) : QWidget(parent) {
  QVBoxLayout* layout = new QVBoxLayout(this);
  layout->setContentsMargins(50, 20, 50, 20);
  layout->setSpacing(0);

  ListWidget* list = new ListWidget(this);
  list->setSpacing(0);

  list->addItem(new ParamValueControlF(
      "CruiseSpeedMin", "최저 설정속도 (km/h)",
      "롱컨을 처음 켤 때 적용되는 최저 설정속도입니다 (km/h). 주행 중 변경하면 약 1초 안에 반영됩니다.",
      "../assets/offroad/icon_road.png", 5, 30, 1, 0, 30, this));

  list->addItem(new ParamControl(
      "ApplyLongDynamicCost", "동적 차간거리 가감속",
      "KRKeegan 방식으로 저속에서 앞차가 멀어질 때 가속 응답을 빠르게 하고, 설정된 차간거리별 MPC 비용을 동적으로 조정합니다.",
      "../assets/offroad/icon_road.png", this));

  list->addItem(new ParamValueControlF(
      "SpeedFromPCM", "크루즈 설정속도 기준",
      "1: 순정 SCC 설정속도 사용 / 2: 오픈파일럿 설정속도와 C3 버튼 모드 사용",
      "../assets/offroad/icon_road.png", 1, 2, 1, 0, 2, this));

  list->addItem(new ParamValueControlF(
      "AutoGasTokSpeed", "가속페달 자동재개 속도 (km/h)",
      "이 속도 이상에서 가속페달 자동재개를 허용합니다.",
      "../assets/offroad/icon_road.png", 5, 60, 1, 0, 30, this));

  list->addItem(new ParamValueControlF(
      "AutoGasCancelSpeed", "가속페달 해제 취소속도 (km/h)",
      "짧은 가속페달 조작 후 이 속도보다 낮으면 자동재개하지 않습니다.",
      "../assets/offroad/icon_road.png", 0, 60, 1, 0, 30, this));

  list->addItem(new ParamValueControlF(
      "CruiseButtonMode", "크루즈 버튼 모드",
      "0: 일반 1km/h 증감 / 1: RES 사용자단위, SET 사용자단위 / 2: SET 현재속도 동기화 / 3: RES 지정속도 순환",
      "../assets/offroad/icon_road.png", 0, 3, 1, 0, 0, this));

  list->addItem(new ParamValueControlF(
      "CruiseSpeedUnit", "크루즈 사용자 증감단위 (km/h)",
      "크루즈 버튼 모드 1~3에서 사용하는 속도 단위입니다.",
      "../assets/offroad/icon_road.png", 1, 20, 1, 0, 10, this));

  list->addItem(new ParamValueControlF(
      "CruiseSpeedUnitBasic", "크루즈 기본 증감단위 (km/h)",
      "크루즈 버튼 모드 0에서 RES/SET 짧게 누르기에 사용하는 속도 단위입니다.",
      "../assets/offroad/icon_road.png", 1, 10, 1, 0, 1, this));

  list->addItem(new ParamValueControlF(
      "CruiseButtonLongDelay", "크루즈 버튼 길게누름 시간",
      "RES/SET 길게누름 판정시간입니다. 제어주기 0.01초 단위이며 기본 70은 약 0.7초입니다.",
      "../assets/offroad/icon_road.png", 30, 150, 5, 0, 70, this));

  const std::array<std::tuple<const char*, const char*, int>, 5> cruise_speed_table = {{
    {"CruiseSpeed1", "크루즈 속도표 1단계 (km/h)", 30},
    {"CruiseSpeed2", "크루즈 속도표 2단계 (km/h)", 50},
    {"CruiseSpeed3", "크루즈 속도표 3단계 (km/h)", 70},
    {"CruiseSpeed4", "크루즈 속도표 4단계 (km/h)", 90},
    {"CruiseSpeed5", "크루즈 속도표 5단계 (km/h)", 110},
  }};
  for (const auto& [key, title, default_value] : cruise_speed_table) {
    list->addItem(new ParamValueControlF(
        key, title, "크루즈 버튼 모드 3에서 RES/SET으로 순환하는 설정속도 단계입니다.",
        "../assets/offroad/icon_road.png", 5, 160, 5, 0, default_value, this));
  }

  list->addItem(new ParamValueControlF(
      "AutoSpeedUptoRoadSpeedLimit", "앞차 자동증속 도로속도 비율 (%)",
      "0은 끔입니다. 앞차가 더 빠르고 60m 이내일 때 일반 도로 제한속도의 지정 비율까지만 설정속도를 올립니다.",
      "../assets/offroad/icon_road.png", 0, 120, 5, 0, 0, this));

  list->addItem(new ParamValueControlF(
      "AutoRoadSpeedAdjust", "도로 제한속도 변경 반영률 (%)",
      "0: 설정속도 유지 / 1~100: 제한속도가 내려갈 때 혼합 적용 / -100: 새 제한속도로 즉시 변경",
      "../assets/offroad/icon_road.png", -100, 100, 10, 0, 0, this));

  list->addItem(new ParamValueControlF(
      "AutoRoadSpeedLimitOffset", "도로 제한속도 오프셋 (km/h)",
      "도로 제한속도 자동변경 모드에서 더하거나 뺄 값입니다.",
      "../assets/offroad/icon_road.png", -30, 30, 1, 0, 0, this));

  list->addItem(new ParamValueControlF(
      "AutoNaviSpeedSafetyFactor", "내비 감속 안전비율 (%)",
      "카메라와 구간단속 목표속도에 적용하는 보정 비율입니다. 100은 보정 없이 적용합니다.",
      "../assets/offroad/icon_road.png", 80, 120, 1, 0, 100, this));

  list->addItem(new ParamControl(
      "AutoGasResumeGuard", "가속페달 자동재개 안전조건",
      "가속페달 해제 자동재개 시 크루즈 가능 상태와 주행 안전조건을 확인합니다.",
      "../assets/offroad/icon_road.png", this));

  list->addItem(new ParamControl(
      "SccSmootherSyncGasPressed", "가속페달 설정속도 동기화",
      "가속페달로 설정속도보다 빠르게 주행하면 현재 차량속도에 맞춰 크루즈 설정속도를 올립니다.",
      "../assets/offroad/icon_road.png", this));

  list->addItem(new ParamValueControlF(
      "AutoResumeFromGas", "가속페달 오토리줌 모드",
      "0: 끔 / 1: 조건 충족 중 재개 / 2: 조건 충족 및 0.4초 미만 짧은 가속 후 재개",
      "../assets/offroad/icon_road.png", 0, 2, 1, 0, 1, this));

  list->addItem(new ParamValueControlF(
      "AutoResumeFromGasSpeedMode", "오토리줌 설정속도 모드",
      "0: 현재속도 / 1: 이전 설정속도 / 2: 앞차가 있으면 이전 설정속도",
      "../assets/offroad/icon_road.png", 0, 2, 1, 0, 0, this));

  list->addItem(new ParamControl(
      "AutoResumeFromBrakeRelease", "브레이크 해제 오토리줌",
      "브레이크를 놓을 때 조향·신호·앞차 거리 또는 속도 안전조건을 만족하면 롱컨을 재개합니다.",
      "../assets/offroad/icon_road.png", this));

  list->addItem(new ParamValueControlF(
      "AutoResumeFromBrakeCarSpeed", "브레이크 해제 재개속도 (km/h)",
      "앞차가 없을 때 이 속도 이상에서만 브레이크 해제 오토리줌을 허용합니다.",
      "../assets/offroad/icon_road.png", 5, 60, 1, 0, 30, this));

  list->addItem(new ParamValueControlF(
      "AutoResumeFromBrakeReleaseDist", "브레이크 해제 앞차거리 (m)",
      "앞차가 있을 때 이 거리 이상에서만 브레이크 해제 오토리줌을 허용합니다.",
      "../assets/offroad/icon_road.png", 2, 50, 1, 0, 10, this));

  ScrollView *scroller = new ScrollView(list, this);
  scroller->setVerticalScrollBarPolicy(Qt::ScrollBarAsNeeded);
  layout->addWidget(scroller);
}
/////////////////////////////////////////////////////////////////////////

LongitudinalPanel::LongitudinalPanel(QWidget* parent) : QWidget(parent) {
  QVBoxLayout* layout = new QVBoxLayout(this);
  layout->setContentsMargins(50, 20, 50, 20);
  layout->setSpacing(0);

  ListWidget* list = new ListWidget(this);
  list->setSpacing(0);

  list->addItem(new ParamValueControlF(
      "TrafficStopMode", "E2E/ACC 조건부 선택",
      "ACC: 신호정지 미사용 / AUTO: 원거리 신호정지·출발준비에서 E2E / APILOT: AUTO 조건과 비전 앞차까지 E2E. 항상 E2E는 Experimental mode 토글을 사용합니다.",
      "../assets/img_experimental_white.svg", 0, 2, 1, 0, 2, this));

  list->addItem(new ParamControl(
      "MixRadarInfo", "레이더·비전 가속도 혼합",
      "레이더 앞차가 매칭되었을 때 비전 모델의 가속도 변화가 더 크면 이를 혼합해 출발·감속 반응을 보완합니다.",
      "../assets/offroad/icon_road.png"));

  list->addItem(new ParamValueControlF(
      "StartAccelApply", "Start Acceleration",
      "정지 후 출발 가속도입니다. 표시값에 0.02m/s²를 곱해 적용합니다.",
      "../assets/offroad/icon_openpilot.png", 0, 100, 10, 0, 0, this));

  list->addItem(new ParamValueControlF(
      "StopAccelApply", "Stop Accel Apply",
      "정지 마무리 제동값입니다. 표시값에 -0.02m/s²를 곱해 적용하며 0은 추가 제동을 끕니다.",
      "../assets/offroad/icon_openpilot.png", 0, 100, 10, 0, 30, this));

  list->addItem(new ParamValueControlF(
      "SoftHoldMode", "Soft Hold Mode",
      "0: 끔, 1: 브레이크를 놓은 뒤 정지 유지, 2: aPilot SCC 호환 모드(일부 차량은 오토홀드/EPB가 작동할 수 있음). 가속페달 또는 RES/+로 해제합니다.",
      "../assets/offroad/icon_openpilot.png", 0, 2, 1, 0, 1, this));

  list->addItem(new ParamValueControlF(
      "TrafficStopAccel", "Traffic Stop Deceleration",
      "신호정지 감속 강도입니다. 낮추면 더 일찍 부드럽게 감속하고, 높이면 더 늦고 강하게 감속합니다. aPilot 기본값은 80%입니다.",
      "../assets/offroad/icon_road.png", 10, 120, 10, 0, 80, this));

  list->addItem(horizontal_line());

  const std::array<std::tuple<const char*, const char*, int>, 6> accel_controls = {{
    {"CruiseMaxVals1", "Max Accel 0 km/h", 160},
    {"CruiseMaxVals2", "Max Accel 40 km/h", 120},
    {"CruiseMaxVals3", "Max Accel 60 km/h", 100},
    {"CruiseMaxVals4", "Max Accel 80 km/h", 80},
    {"CruiseMaxVals5", "Max Accel 110 km/h", 70},
    {"CruiseMaxVals6", "Max Accel 140 km/h", 60},
  }};
  for (const auto& [key, title, default_value] : accel_controls) {
    list->addItem(new ParamValueControlF(
        key, title, "해당 속도 구간의 최대 크루즈 가속도입니다 (×0.01m/s²).",
        "../assets/offroad/icon_openpilot.png", 10, 250, 1, 0, default_value, this));
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
        "../assets/offroad/icon_openpilot.png", 70, 300, 1, 0, default_value, this));
  }

  list->addItem(new ParamValueControlF(
      "TFollowSpeedRatio", "TR Speed Ratio",
      "속도에 따라 추종시간을 늘리는 비율입니다 (%).",
      "../assets/offroad/icon_openpilot.png", 100, 300, 5, 0, 120, this));
  list->addItem(new ParamValueControlF(
      "PrevCruiseGap", "Cruise Gap",
      "마지막 선택 GAP을 저장하고 다음 주행에서도 복원합니다.",
      "../assets/offroad/icon_openpilot.png", 1, 4, 1, 0, 4, this));
  list->addItem(new ParamValueControlF(
      "MySafeModeFactor", "SAFE TR Factor",
      "ECO/SAFE 모드의 C2 추종거리 보정 기준입니다 (%).",
      "../assets/offroad/icon_openpilot.png", 50, 100, 5, 0, 80, this));
  list->addItem(new ParamValueControlF(
      "MyEcoModeFactor", "ECO Accel Factor",
      "ECO 모드 최대가속 비율이며 SAFE는 이 값과 SAFE 비율을 함께 적용합니다 (%).",
      "../assets/offroad/icon_openpilot.png", 10, 95, 5, 0, 80, this));
  list->addItem(new ParamValueControlF(
      "InitMyDrivingMode", "Initial Driving Mode",
      "부팅 모드입니다. 1:ECO, 2:SAFE, 3:NORMAL, 4:HIGH, 5:AUTO.",
      "../assets/offroad/icon_openpilot.png", 1, 5, 1, 0, 3, this));

  list->addItem(horizontal_line());

  list->addItem(new ParamValueControlF(
      "LongTuningKpV", "Longitudinal Kp", "속도 오차 비례 게인입니다 (×0.01).",
      "../assets/offroad/icon_openpilot.png", 0, 200, 5, 0, 100, this));
  list->addItem(new ParamValueControlF(
      "LongTuningKiV", "Longitudinal Ki", "누적 속도 오차 적분 게인입니다 (×0.001).",
      "../assets/offroad/icon_openpilot.png", 0, 2000, 5, 0, 200, this));
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
      "StopDistance", "Stop Distance (cm)",
      "ACC와 E2E에 공통으로 적용되는 정지 유지거리입니다. aPilot 기본값은 600cm입니다.",
      "../assets/offroad/icon_road.png", 200, 1000, 50, 0, 600, this));

  ScrollView *scroller = new ScrollView(list, this);
  scroller->setVerticalScrollBarPolicy(Qt::ScrollBarAsNeeded);
  layout->addWidget(scroller);
}

/////////////////////////////////////////////////////////////////////////

UISettingsPanel::UISettingsPanel(QWidget* parent) : QWidget(parent) {
  QVBoxLayout* layout = new QVBoxLayout(this);
  layout->setContentsMargins(50, 20, 50, 20);
  layout->setSpacing(0);

  ListWidget* list = new ListWidget(this);
  list->setSpacing(0);

  list->addItem(new ParamControl(
      "ShowCarrotHud", "좌측 HUD 박스 표시",
      "속도·크루즈·GAP·기어·주행모드·제한속도 HUD를 표시합니다.",
      "../assets/offroad/icon_road.png", this));
  list->addItem(new ParamControl(
      "ShowGearAnimation", "기어 팝업 애니메이션",
      "변속단이 바뀔 때 중앙 팝업 애니메이션을 표시합니다.",
      "../assets/offroad/icon_road.png", this));
  list->addItem(new ParamValueControlF(
      "ShowDateTime", "날짜·시간 표시",
      "0: 끔 / 1: 시간+날짜 / 2: 시간 / 3: 날짜",
      "../assets/offroad/icon_road.png", 0, 3, 1, 0, 1, this));
  list->addItem(new ParamControl(
      "ShowDebugUI", "디버그 UI 표시",
      "주행 화면의 개발자 디버그 정보를 표시합니다.",
      "../assets/offroad/icon_shell.png", this));
  list->addItem(new ParamControl(
      "ShowBlindSpotAlways", "BSD 영역 상시 표시",
      "BSD 감지가 없을 때도 좌우 사각지대 영역을 흐리게 표시합니다.",
      "../assets/offroad/icon_road.png", this));

  list->addItem(horizontal_line());
  auto *status_color = new ParamControl(
      "ShowPathStatusColor", "주행 경로 상태색",
      "활성=녹색, 정속=노란색, 가속=주황색, 감속=빨간색, 비활성=검은색으로 표시합니다.",
      "../assets/offroad/icon_road.png", this);
  status_color->showDescription();
  list->addItem(status_color);
  list->addItem(new ParamValueControlF(
      "ShowPathWidth", "주행 경로 폭 (cm)",
      "차량 중심에서 경로 한쪽 끝까지의 폭입니다. 90은 좌우 각각 0.90m입니다.",
      "../assets/offroad/icon_road.png", 30, 150, 10, 0, 90, this));

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
      "켜면 아래 토크 수동값을 적용합니다.",
      "../assets/offroad/icon_openpilot.png", this));
  
  list->addItem(new NtuneValueControl("torque", "latAccelFactor",
      "Lat Accel Factor",
      "횡가속도 대비 토크 계수입니다. 작을수록 조향 토크가 강해집니다.\n"
      "범위: 0.50 ~ 4.50  /  기본값: 2.70",
      "../assets/offroad/icon_openpilot.png", 0.5, 4.5, 0.05, 2, 2.7, this));

  list->addItem(new NtuneValueControl("torque", "friction",
      "Friction",
      "정지마찰 보상값입니다. 크면 중앙 부근 응답이 빨라지지만\n"
      "너무 크면 직진에서 좌우로 흔들립니다.\n"
      "범위: 0.000 ~ 0.200  /  기본값: 0.080",
      "../assets/offroad/icon_openpilot.png", 0.0, 0.2, 0.005, 3, 0.08, this));

  list->addItem(new ParamValueControlF("LateralTorqueKpV",
      "Torque Kp", "수동 비례 게인 (×0.01). 기본값: 10 (=0.10)",
      "../assets/offroad/icon_openpilot.png", 0, 500, 5, 0, 10, this));

  list->addItem(new ParamValueControlF("LateralTorqueKiV",
      "Torque Ki", "수동 적분 게인 (×0.01). 기본값: 10 (=0.10)",
      "../assets/offroad/icon_openpilot.png", 0, 200, 1, 0, 10, this));

  list->addItem(new ParamValueControlF("LateralTorqueKf",
      "Torque Kf", "수동 피드포워드 게인 (×0.01). 기본값: 100 (=1.00)",
      "../assets/offroad/icon_openpilot.png", 0, 200, 5, 0, 100, this));

  list->addItem(new ParamValueControlF("LateralTorqueKd",
      "Torque Kd", "수동 미분 게인 (×0.01). 기본값: 0",
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

  ScrollView *scroller = new ScrollView(list, this);
  scroller->setVerticalScrollBarPolicy(Qt::ScrollBarAsNeeded);
  layout->addWidget(scroller);
}

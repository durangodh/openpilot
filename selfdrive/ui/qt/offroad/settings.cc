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

// â”€â”€ Offset Total Control â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

  minus_btn = new QPushButton("âˆ’");
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

// â”€â”€ AdjustLaneOffset Control â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

  minus_btn = new QPushButton("âˆ’");
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
  val += delta * 5;                       // 5cm ë‹¨ìœ„
  val = std::max(0, std::min(40, val));   // 0 ~ 40cm (ë‚´ë¶€ í´ë¦¬í•‘ 0.4m)
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

// â”€â”€ AutoLaneChangeTimer Control â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
AutoLaneChangeTimerControl::AutoLaneChangeTimerControl(const QString &title,
                                                       const QString &desc,
                                                       const QString &icon,
                                                       QWidget *parent)
    : AbstractControl(title, desc, icon, parent) {

  // ë²„íŠ¼ì„ ì œëª© ì•„ë˜ì— ë°°ì¹˜
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

// â”€â”€ DynamicLaneProfile Control â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
DynamicLaneProfileControl::DynamicLaneProfileControl(const QString &title,
                                                     const QString &desc,
                                                     const QString &icon,
                                                     QWidget *parent)
    : AbstractControl(title, desc, icon, parent) {

  // ë²„íŠ¼ì„ ì œëª© ì•„ë˜ì— ë°°ì¹˜
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

// â”€â”€ nTune steering parameter helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
    // nTune.write_configì™€ ë™ì¼í•˜ê²Œ 0666 ê¶Œí•œ ìœ ì§€
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

// í† í¬ê°’(latAccelFactor/friction)ì€ nTune JSON ì´ ì•„ë‹ˆë¼ Params ì— ìˆë‹¤.
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

// â”€â”€ Param Value Control (ì •ìˆ˜ Params) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

  minus_btn = new QPushButton("âˆ’");
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

// â”€â”€ nTune Value Control â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

  minus_btn = new QPushButton("âˆ’");
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
  // ë¶€ë™ì†Œìˆ˜ ì˜¤ì°¨ ì •ë¦¬
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
      "ì˜¤í”ˆíŒŒì¼ëŸ¿ ì‚¬ìš©",
      "ì¼œì§: ì°¨ê°„ê±°ë¦¬Â·ì†ë„ ë° ì°¨ì„ ìœ ì§€ ë³´ì¡°ë¥¼ ì‚¬ìš©í•©ë‹ˆë‹¤. í•­ìƒ ì „ë°©ì„ ì£¼ì‹œí•´ì•¼ í•˜ë©° ì„¤ì •ì€ ì°¨ëŸ‰ ì „ì›ì´ êº¼ì§„ ë’¤ ì ìš©ë©ë‹ˆë‹¤.",
      "../assets/offroad/icon_openpilot.png",
    },
    {
      "IsLdwEnabled",
      "ì°¨ì„ ì´íƒˆ ê²½ê³  ì‚¬ìš©",
      "ì¼œì§: 50km/h ì´ìƒì—ì„œ ë°©í–¥ì§€ì‹œë“± ì—†ì´ ì°¨ì„ ì„ ë²—ì–´ë‚˜ë©´ ê²½ê³ í•©ë‹ˆë‹¤.",
      "../assets/offroad/icon_warning.png",
    },
    {
      "IsRHD",
      "ìš°í•¸ë“¤ ì°¨ëŸ‰ ëª¨ë“œ",
      "ì¼œì§: ì¢Œì¸¡í†µí–‰ ê·œì¹™ê³¼ ìš°ì¸¡ ìš´ì „ì„ ê¸°ì¤€ ìš´ì „ì ëª¨ë‹ˆí„°ë§ì„ ì‚¬ìš©í•©ë‹ˆë‹¤. êµ­ë‚´ ì¢Œí•¸ë“¤ ì°¨ëŸ‰ì€ ë•ë‹ˆë‹¤.",
      "../assets/offroad/icon_openpilot_mirrored.png",
    },
    {
      "IsMetric",
      "ë¯¸í„°ë²• ì‚¬ìš©",
      "ì¼œì§: ì†ë„ë¥¼ km/hë¡œ í‘œì‹œ / êº¼ì§: mphë¡œ í‘œì‹œí•©ë‹ˆë‹¤.",
      "../assets/offroad/icon_metric.png",
    },
    {
      "RecordFront",
      "ìš´ì „ì ì¹´ë©”ë¼ ë…¹í™”Â·ì—…ë¡œë“œ",
      "ì¼œì§: ìš´ì „ì ëª¨ë‹ˆí„°ë§ ê°œì„ ì„ ìœ„í•´ ì‹¤ë‚´ ì¹´ë©”ë¼ ì˜ìƒì„ ë…¹í™”í•˜ê³  ì—…ë¡œë“œí•©ë‹ˆë‹¤.",
      "../assets/offroad/icon_monitoring.png",
    },
    {
      "ExperimentalMode",
      "ì‹¤í—˜ëª¨ë“œ",
      "ì¼œì§: E2E ì¢…ë°©í–¥ ì œì–´ ë“± ì•ŒíŒŒ ìˆ˜ì¤€ ê¸°ëŠ¥ì„ ì‚¬ìš©í•©ë‹ˆë‹¤. ì‹œí—˜ ê¸°ëŠ¥ì´ë¯€ë¡œ ì•ˆì „ ê²½ê³ ë¥¼ í™•ì¸í•˜ì‹­ì‹œì˜¤.",
      "../assets/img_experimental_white.svg",
    },
    {
      "ExperimentalLongitudinalEnabled",
      "ì˜¤í”ˆíŒŒì¼ëŸ¿ ì‹¤í—˜ ì¢…ë°©í–¥ ì œì–´",
      "<b>ì£¼ì˜: ì´ ì°¨ëŸ‰ì˜ ì¢…ë°©í–¥ ì œì–´ëŠ” ì‹œí—˜ ê¸°ëŠ¥ì´ë©° ìˆœì • AEBê°€ ë¹„í™œì„±í™”ë  ìˆ˜ ìˆìŠµë‹ˆë‹¤.</b><br>\
          ì¼œì§: ìˆœì • ACC ëŒ€ì‹  ì˜¤í”ˆíŒŒì¼ëŸ¿ì´ ê°€ì†ê³¼ ì œë™ì„ ì œì–´í•©ë‹ˆë‹¤.",
      "../assets/offroad/icon_speed_limit.png",
    },
#ifdef ENABLE_MAPS
    {
      "NavSettingTime24h",
      "24ì‹œê°„ í˜•ì‹ ì‚¬ìš©",
      "ì¼œì§: ë„ì°©ì˜ˆì •ì‹œê°„ì„ 24ì‹œê°„ì œë¡œ í‘œì‹œ / êº¼ì§: ì˜¤ì „Â·ì˜¤í›„ í˜•ì‹ìœ¼ë¡œ í‘œì‹œí•©ë‹ˆë‹¤.",
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
    ì˜¤í”ˆíŒŒì¼ëŸ¿ì€ ê¸°ë³¸ì ìœ¼ë¡œ ì•ˆì •ì ì¸ <b>ì¼ë°˜ëª¨ë“œ</b>ë¡œ ì£¼í–‰í•©ë‹ˆë‹¤.\
    ì‹¤í—˜ëª¨ë“œëŠ” ì•„ì§ ì¼ë°˜ëª¨ë“œì— í¬í•¨ë˜ì§€ ì•Šì€ <b>ì•ŒíŒŒ ìˆ˜ì¤€ ê¸°ëŠ¥</b>ì„ í™œì„±í™”í•©ë‹ˆë‹¤.\
    ì£¼ìš” ì‹¤í—˜ ê¸°ëŠ¥:\
    <br> \
    <h4>ğŸŒ® E2E ì¢…ë°©í–¥ ì œì–´ ğŸŒ®</h4> \
    ì£¼í–‰ ëª¨ë¸ì´ ê°€ì†ê³¼ ì œë™ì„ ì œì–´í•˜ë©° ì ìƒ‰ ì‹ í˜¸ì™€ ì •ì§€í‘œì§€íŒ ì •ì§€ë¥¼ í¬í•¨í•œ ì‚¬ëŒê³¼ ìœ ì‚¬í•œ ì£¼í–‰ì„ ì‹œë„í•©ë‹ˆë‹¤.");

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
      const QString no_long = "í˜„ì¬ ì°¨ëŸ‰ì—ì„œëŠ” ì˜¤í”ˆíŒŒì¼ëŸ¿ ì¢…ë°©í–¥ ì œì–´ë¥¼ ì‚¬ìš©í•  ìˆ˜ ì—†ìŠµë‹ˆë‹¤.";
      const QString exp_long = "ë¨¼ì € ì˜¤í”ˆíŒŒì¼ëŸ¿ ì‹¤í—˜ ì¢…ë°©í–¥ ì œì–´ë¥¼ ì¼œì‹­ì‹œì˜¤.";
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
      "openpilot requires the device to be mounted within 4Â° left or right and "
      "within 5Â° up or 8Â° down. openpilot is continuously calibrating, resetting is rarely required.";
  std::string calib_bytes = Params().get("CalibrationParams");
  if (!calib_bytes.empty()) {
    try {
      AlignedBuffer aligned_buf;
      capnp::FlatArrayMessageReader cmsg(aligned_buf.align(calib_bytes.data(), calib_bytes.size()));
      auto calib = cmsg.getRoot<cereal::Event>().getLiveCalibration();
      if (calib.getCalStatus() != 0) {
        double pitch = calib.getRpyCalib()[1] * (180 / M_PI);
        double yaw = calib.getRpyCalib()[2] * (180 / M_PI);
        desc += QString(" Your device is pointed %1Â° %2 and %3Â° %4.")
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

  QPushButton *close_btn = new QPushButton("â† Back");
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
    {"UI ì„¤ì •", new UISettingsPanel(this)},
    {"ì¡°í–¥", new VIPPanel(this)},
    {"Cruise", new CruisePanel(this)},
    {"ë¡±ì»¨", new LongitudinalPanel(this)},
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
                                            "ê³„ê¸°íŒ ì†ë„ ì‚¬ìš©",
                                            "ì¼œì§: ì°¨ëŸ‰ ê³„ê¸°íŒ ì†ë„ë¥¼ ì‚¬ìš©í•©ë‹ˆë‹¤. / êº¼ì§: íœ  ì†ë„ë¥¼ ì‚¬ìš©í•©ë‹ˆë‹¤.",
                                            "../assets/offroad/icon_road.png",
                                            this));
  toggles.append(new ParamControl("LongControlEnabled",
                                            "í˜„ëŒ€Â·ê¸°ì•„ ì¢…ë°©í–¥ ì œì–´",
                                            "ì¼œì§: ì˜¤í”ˆíŒŒì¼ëŸ¿ì´ ê°€ì†ê³¼ ì œë™ì„ ì œì–´í•©ë‹ˆë‹¤. ì‹œí—˜ ê¸°ëŠ¥ì´ë¯€ë¡œ ì£¼í–‰ ì¤‘ í•­ìƒ ì „ë°©ì„ í™•ì¸í•˜ì‹­ì‹œì˜¤.",
                                            "../assets/offroad/icon_road.png",
                                            this));
  toggles.append(new ParamControl("IsLdwsCar",
                                            "LDWS",
                                            "ì°¨ëŸ‰ì´ ì°¨ì„ ì´íƒˆ ê²½ê³ (LDWS)ë§Œ ì§€ì›í•  ë•Œ ì¼­ë‹ˆë‹¤. LKAS ì°¨ëŸ‰ì€ ë•ë‹ˆë‹¤.",
                                            "../assets/offroad/icon_openpilot.png",
                                            this));
  toggles.append(new ParamControl("LaneChangeEnabled",
                                            "ì°¨ì„ ë³€ê²½ ë³´ì¡°",
                                            "ì¼œì§: ë°©í–¥ì§€ì‹œë“±ê³¼ ìš´ì „ì ì¡°í–¥ ì…ë ¥ìœ¼ë¡œ ì°¨ì„ ë³€ê²½ì„ ë³´ì¡°í•©ë‹ˆë‹¤. ì£¼ë³€ ì°¨ëŸ‰ì˜ ì•ˆì „ ì—¬ë¶€ëŠ” ìš´ì „ìê°€ ì§ì ‘ í™•ì¸í•´ì•¼ í•©ë‹ˆë‹¤.",
                                            "../assets/offroad/icon_road.png",
                                            this));
  toggles.append(new ParamControl("AutoLaneChangeEnabled",
                                            "ìë™ ì°¨ì„ ë³€ê²½(ì¡°í–¥ ì…ë ¥ ì—†ìŒ)",
                                            "ì¼œì§: ë°©í–¥ì§€ì‹œë“± ì‘ë™ í›„ ë³„ë„ì˜ í•¸ë“¤ ì…ë ¥ ì—†ì´ ì°¨ì„ ë³€ê²½ì„ ì‹œì‘í•©ë‹ˆë‹¤. ì‹œí—˜ ê¸°ëŠ¥ì´ë¯€ë¡œ ì£¼ë³€ì„ ì§ì ‘ í™•ì¸í•˜ì‹­ì‹œì˜¤.",
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
                                           "ìµœì‹  ë¹„ì „Â·ì§€ë„ ì»¤ë¸Œê°ì†",
                                           "ì¼œì§: ë¹„ì „ ëª¨ë¸ê³¼ í‹°ë§µ ê²½ë¡œ ì¤‘ ë” ë‚®ì€ ì»¤ë¸Œ ëª©í‘œì†ë„ë¥¼ ì ìš©í•©ë‹ˆë‹¤. / êº¼ì§: ì»¤ë¸Œ ìë™ê°ì†ì„ ì‚¬ìš©í•˜ì§€ ì•ŠìŠµë‹ˆë‹¤.",
                                            "../assets/offroad/icon_road.png",
                                            this));
  toggleLayout->addWidget(new ParamValueControlF(
      "AutoCurveSpeedFactor", "ë¹„ì „ ì»¤ë¸Œ ê°ì†ë¹„ìœ¨",
      "ë¹„ì „ ëª¨ë¸ì˜ ì»¤ë¸Œ íŒë‹¨ ê°•ë„ì…ë‹ˆë‹¤. ê°’ ì¦ê°€(+): ì»¤ë¸Œì—ì„œ ë” ë§ì´ ê°ì† / ê°’ ê°ì†Œ(-): ê°ì†ì„ ì¤„ì„.",
      "../assets/offroad/icon_road.png", 50, 300, 5, 0, 120, this));
  toggleLayout->addWidget(new ParamValueControlF(
      "AutoCurveSpeedLowerLimit", "ì»¤ë¸Œ ìµœì €ì†ë„",
      "ë¹„ì „Â·í‹°ë§µ ì»¤ë¸Œ ëª©í‘œì†ë„ì˜ í•˜í•œì…ë‹ˆë‹¤. ê°’ ì¦ê°€(+): ì»¤ë¸Œ ì†ë„ê°€ ë¹¨ë¼ì§ / ê°’ ê°ì†Œ(-): ë” ë‚®ì€ ì†ë„ê¹Œì§€ ê°ì†.",
      "../assets/offroad/icon_speed_limit.png", 5, 80, 5, 0, 30, this));
  toggleLayout->addWidget(new ParamValueControlF(
      "MapTurnSpeedFactor", "í‹°ë§µ ì§€ë„ ì»¤ë¸Œì†ë„ ë¹„ìœ¨",
      "í‹°ë§µ ê²½ë¡œì˜ ì»¤ë¸Œ ëª©í‘œì†ë„ ë¹„ìœ¨ì…ë‹ˆë‹¤. ê°’ ì¦ê°€(+): ì»¤ë¸Œ ì†ë„ê°€ ë¹¨ë¼ì§€ê³  ê°ì†ì´ ì¤„ì–´ë“¦ / ê°’ ê°ì†Œ(-): ë” ëŠë¦¬ê²Œ í†µê³¼.",
      "../assets/offroad/icon_road.png", 50, 150, 5, 0, 90, this));
  toggleLayout->addWidget(new ParamValueControlF(
      "AutoNaviSpeedDecelRate", "ì§€ë„ ì»¤ë¸Œ ê°ì†ë„",
      "í‹°ë§µ ì»¤ë¸Œ ì§„ì… ê°ì† ê°•ë„(Ã—0.01m/sÂ²)ì…ë‹ˆë‹¤. ê°’ ì¦ê°€(+): ëŠ¦ê³  ê°•í•˜ê²Œ ê°ì† / ê°’ ê°ì†Œ(-): ì¼ì° ë¶€ë“œëŸ½ê²Œ ê°ì†.",
      "../assets/offroad/icon_road.png", 10, 300, 10, 0, 120, this));
  toggleLayout->addWidget(horizontal_line());
  toggleLayout->addWidget(new ParamControl("SccSmootherSyncGasPressed",
                                            "ê°€ì†í˜ë‹¬ ì„¤ì •ì†ë„ ë™ê¸°í™”",
                                            "ì¼œì§: ê°€ì†í˜ë‹¬ë¡œ ì„¤ì •ì†ë„ë³´ë‹¤ ë¹¨ë¼ì§€ë©´ í˜„ì¬ ì°¨ëŸ‰ì†ë„ë¡œ ì„¤ì •ì†ë„ë¥¼ ì˜¬ë¦½ë‹ˆë‹¤.",
                                            "../assets/offroad/icon_road.png",
                                            this));
  toggleLayout->addWidget(horizontal_line());
  toggleLayout->addWidget(new ParamControl("StockNaviDecelEnabled",
                                            "ìˆœì • ë‚´ë¹„ ê¸°ë°˜ ê°ì†",
                                            "ì¼œì§: ìˆœì • ë‚´ë¹„ê²Œì´ì…˜ì˜ ì œí•œì†ë„Â·ì¹´ë©”ë¼ ì •ë³´ë¥¼ ì¢…ë°©í–¥ ê°ì†ì— ì‚¬ìš©í•©ë‹ˆë‹¤.",
                                            "../assets/offroad/icon_road.png",
         /9÷‹h‘éì¶»§q«^w&Ô6öçG&öÂ‚$ÆFW&ÅF÷'VT7W7FöÒ"À¢.ØjØÂÈ‰¸ùÈJNÊ	RÈ*ÎÉª’"À¢.ËÉÎÊy¢ÉXN¹é‚ØjØÂÈ‰¸ù«	"ÊÉª’ò««ÎÊy¢Ë
¹ø’«‹»;‚¹‰¸©BÉé¸ù’ÈJNÊ	^«	"È*ÎÉª’â"À¢"ââö76WG2ööfg&öBö–6öåö÷Vç–Æ÷Bçær"ÂF†—2’“°¢ ¢Æ—7BÓæFD—FVÒ†æWrçGVæUfÇVT6öçG&öÂ‚'F÷'VR"Â&ÆD66VÄf7F÷""À¢.Ùª«ÈhÒØjØÎ«8NÈ‰‚"À¢.Ùª«ÈhŞ¸øB¸È»˜BØjØÂ«8NÈ‰Éè^¸¸¸ºBâ«	"ÊiŞ«‚²“¢ÊÙj^ÉÛBÉ[ŞÙ[NÊyò«	"«	ÈhÂ‚Ò“¢ÊÙj^ÉÛB«	^Ù[NÊyåÆâ ¢.»)NÉÈC¢ãSâBãSò«‹»;«	#¢"ãs"À¢"ââö76WG2ööfg&öBö–6öåö÷Vç–Æ÷Bçær"ÂãRÂBãRÂãRÂ"Â"ãrÂF†—2’“° ¢Æ—7BÓæFD—FVÒ†æWrçGVæUfÇVT6öçG&öÂ‚'F÷'VR"Â&g&–7F–öâ"À¢.ÊÙjRºxË»;NÈ8"À¢.Ê	^ÊxºxË»;NÈ8«	.Éè^¸¸¸ºBâ«	"ÊiŞ«‚²“¢ÊIÉY’»h«{ÂÊÙjR»	ÉÙÉÛB»š¹ÛÎÊyò«	"«	ÈhÂ‚Ò“¢»	ÉÙÉÛB»h¹9Î¹ûŞ«:¸©º
NÊyåÆâ ¢.¸HºËBØÎº›BÊxÊxNÉyÈIÂÊ(ÎÉ«ºÂÙÙN¹:NºkŞ¸¸¸ºBåÆâ ¢.»)NÉÈC¢ãâã#ò«‹»;«	#¢ãƒ"À¢"ââö76WG2ööfg&öBö–6öåö÷Vç–Æ÷Bçær"ÂãÂã"ÂãRÂ2Âã‚ÂF†—2’“° ¢Æ—7BÓæFD—FVÒ†æWr&ÕfÇVT6öçG&öÄb‚$ÆFW&ÅF÷'VT·b"À¢.ØjØÂ»˜Nº«(ÎÉÛ‚·"Â.ÙˆNÉêÂÊÙj^ÉŠNË
‚»	ÉÙ«	"Œ9sãÉè^¸¸¸ºBâ«	"ÊiŞ«‚²“¢ÊÙjR»	ÉÙÉÛB«	^ÙY«:»šºhBò«	"«	ÈhÂ‚Ò“¢»h¹9Î¹ûŞ«:¸©ºkÂâ«‹»;«	#¢â"À¢"ââö76WG2ööfg&öBö–6öåö÷Vç–Æ÷Bçær"ÂÂSÂRÂÂÂF†—2’“° ¢Æ—7BÓæFD—FVÒ†æWr&ÕfÇVT6öçG&öÄb‚$ÆFW&ÅF÷'VT¶•b"À¢.ØjØÂÊ»hN«(ÎÉÛ‚¶’"Â.¸ˆNÊÊÙj^ÉŠNË
‚»;NÊ	^«	"Œ9sãÉè^¸¸¸ºBâ«	"ÊiŞ«‚²“¢ÊxÈhÒÉŠNË
º[Â»šºjÂ»;NÊ	Rò«	"«	ÈhÂ‚Ò“¢Ë)ÎË)ÎÙè‚»;NÊ	Râ«‹»;«	#¢â"À¢"ââö76WG2ööfg&öBö–6öåö÷Vç–Æ÷Bçær"ÂÂ#ÂÂÂÂF†—2’“° ¢Æ—7BÓæFD—FVÒ†æWr&ÕfÇVT6öçG&öÄb‚$ÆFW&ÅF÷'VT¶b"À¢.ØjØÂÙKÎ¹9ÎØúÎÉ¸Î¹9Â¶b"Â.ºªÙÂÊÙj^ØjØÂ»	Éˆ«	"Œ9sãÉè^¸¸¸ºBâ«	"ÊiŞ«‚²“¢ÊNË+BÊÙjRº¨^ºÉÛB«	^Ù[NÊyò«	"«	ÈhÂ‚Ò“¢É[ŞÙ[NÊyâ«‹»;«	#¢â"À¢"ââö76WG2ööfg&öBö–6öåö÷Vç–Æ÷Bçær"ÂÂ#ÂRÂÂÂF†—2’“° ¢Æ—7BÓæFD—FVÒ†æWr&ÕfÇVT6öçG&öÄb‚$ÆFW&ÅF÷'VT¶B"À¢.ØjØÂºû»hN«(ÎÉÛ‚¶B"Â.«ˆ«*ÙYÂÊÙj^»8Ù™BÉk^Ê	Î«	"Œ9sãÉè^¸¸¸ºBâ«	"ÊiŞ«‚²“¢»8Ù™N«Ék^Ê	Î¹	ÉkBÉXÊ	^ÊÉÛN¸)‚¹NÙ[NÊyò«	"«	ÈhÂ‚Ò“¢»	ÉÙÉÛB»š¹ÛÎÊyâ«‹»;«	#¢â"À¢"ââö76WG2ööfg&öBö–6öåö÷Vç–Æ÷Bçær"ÂÂ#ÂRÂÂÂF†—2’“° ¢Æ—7BÓæFD—FVÒ†æWr&ÕfÇVT6öçG&öÄb‚$ÆD66VÄg&–7F–öäf7F÷""À¢.ºxË»;NÈ8Ùª«ÈhÒ»˜NÉÊ‚"À¢.Ùª«ÈhŞ¸øBÉŠNË
º[ÂºxË»;NÈ8Éy»	ÉˆÙY¸©B»˜NÉÊ‚Œ9sãÉè^¸¸¸ºBâ«	"ÊiŞ«‚²“¢ËºN»ˆÂÊÙjR»	ÉÙÉÛB«	^Ù[NÊyò«	"«	ÈhÂ‚Ò“¢»h¹9Î¹úÎÉ¸ÎÊyâ«‹»;«	#¢sâ"À¢"ââö76WG2ööfg&öBö–6öåö÷Vç–Æ÷Bçær"ÂÂ3ÂRÂÂsÂF†—2’“° ¢Æ—7BÓæFD—FVÒ†æWr&ÕfÇVT6öçG&öÄb‚$ÆD¦W&´g&–7F–öäf7F÷""À¢.ºxË»;NÈ8ÙªÊØÂ»˜NÉÊ‚"À¢.ÉˆËŠÙªÊØÂ»	Éˆ»˜NÉÊ‚Œ9sãÉè^¸¸¸ºBâ«	"ÊiŞ«‚²“¢ËºN»ˆÂÊxNÉèRÊÙj^ÉÛB»š¹ÛÎÊyò«	"«	ÈhÂ‚Ò“¢ÊxNÉèR»	ÉÙÉÛB¸©º
NÊyò¢È*ÎÉª’ÉX‚ÙZ‚â«‹»;«	#¢Câ"À¢"ââö76WG2ööfg&öBö–6öåö÷Vç–Æ÷Bçær"ÂÂ#ÂRÂÂCÂF†—2’“° ¢Æ—7BÓæFD—FVÒ††÷&—¦öçFÅöÆ–æR‚’“° ¢òò)H)Höfg6WBF÷FÂ)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H ¢òòºÉÛºª¹9Â²ºÉÛºjÎÈªBºª¹9Âºª¹ÊÉª’âãÒ¸ºÉÈBÂÓãâ³ãĞ¢Æ—7BÓæFD—FVÒ††÷&—¦öçFÅöÆ–æR‚’“° ¢WFò§F…ööfg6WBÒæWröfg6WEF÷FÄ6öçG&öÂ€¢.Øk^ÙZ’«+ŞºÂÊ(ÎÉ«»;NÊ	R"À¢.Ê;ÎÙh’«+ŞºÂÊ(ÎÉ«Øk^ÙZ’»;NÊ	^«	.Éè^¸¸¸ºBâºÉÛºª¹9Ì+~ºÉÛºjÎÈªBºª¹ÊÉª¹
¸¸¸ºBåÆâ ¢.Ë›Nº™N¹ÛÂÉŠNÙHNÈX¾ÉØÙY¹9ÎÉºÉkB«‹»;«	.ÉËÎºÂ«:Ê	^¹	«:ÉÛB«	"ÙY¸)ºÂÊÊ	^ÙZ¸¸¸ºBåÆâ ¢.É›ÎÊ«ŞÉËÎºÂÉÛN¸ù“¢ÉiÈ‰‚‚²’òÉŠNº[Ê«ŞÉËÎºÂÉÛN¸ù“¢ÉØÎÈ‰‚(‰"•Æâ ¢.»)NÉÈC¢(‰#ãâ³ãÒò«‹»;«	#¢ãÒ"À¢"ââö76WG2ööfg&öBö–6öå÷&öBçær"À¢F†—2“°¢F…ööfg6WBÓç6†÷tFW67&—F–öâ‚“°¢Æ—7BÓæFD—FVÒ‡F…ööfg6WB“° ¢Æ—7BÓæFD—FVÒ††÷&—¦öçFÅöÆ–æR‚’“° ¢òò)H)HF§W7BÆæRöfg6WB)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H ¢WFò¦ÆæUööfg6WBÒæWrF§W7DÆæTöfg6WD6öçG&öÂ€¢.Ë
ÈJÉzÎÉÊ«;^«BÉé¸ù»;NÊ	R"À¢.Ê(ÎÉ«ÉzÎÉÊ«;^«NÉÛB»˜N¸ÈËšŞÉÛÂ¹XÂÉzÎÉÊÉè¸©BÊ«ŞÉËÎºÂ«+ŞºÎº[ÂÉŠî«˜¸¸¸ºBåÆâ ¢.Ê(ÉØ¸øNºÎÉyÈIÂ¸ÈÙ‰^Ë
‚ÉˆnÉØBÊx¸*¹XÂÙª«;Î«ÉèÈ«^¸¸¸ºBåÆâ ¢.ÉiÊ«Ò¸ºBÉzÎÉÊ«Éè«¸)‚ÉiÊ«Ò¸ºBÊ(ÉËÎº›B¸ùÉéÙYÊxÉX®È«^¸¸¸ºBåÆâ ¢.«	"ÊiŞ«‚²“¢ÉzÎÉÊÉè¸©BÊ«ŞÉËÎºÂ¸ÙBºxîÉÛBÉÛN¸ù’ò«	"«	ÈhÂ‚Ò“¢ÉÛN¸ù¹øÉÛBÊHNÉkN¹:Ş¸¸¸ºBåÆâ ¢.»)NÉÈC¢âC6ÒƒV6Ò¸ºÉÈB’ò«‹»;«	#¢ôdb"À¢"ââö76WG2ööfg&öBö–6öå÷&öBçær"À¢F†—2“°¢ÆæUööfg6WBÓç6†÷tFW67&—F–öâ‚“°¢Æ—7BÓæFD—FVÒ†ÆæUööfg6WB“°  ¢Æ—7BÓæFD—FVÒ††÷&—¦öçFÅöÆ–æR‚’“°¢WFò¦FÇö6öçG&öÂÒæWrG–æÖ–4ÆæU&öf–ÆT6öçG&öÂ€¢.¸ùÊË
ÈJºª¹9Â"À¢.Ë
ÈJÈ*ÎÉª“¢ÙZŞÈ8Ë
ÈJ«‹»	‚òË
ÈJºûÈ*ÎÉª“¢ÙZŞÈ8S$R«+ŞºÂòÉé¸ù“¢Ë
ÈJÉÛÈ¹ŞºZÉy¹K¹ÛÂÉé¸ù’ÊNÙ™‚â"À¢"ââö76WG2ööfg&öBö–6öå÷&öBçær"À¢F†—2“°¢FÇö6öçG&öÂÓç6†÷tFW67&—F–öâ‚“°¢Æ—7BÓæFD—FVÒ†FÇö6öçG&öÂ“° ¢Æ—7BÓæFD—FVÒ††÷&—¦öçFÅöÆ–æR‚’“° ¢òò)H)HWFôÆæT6†ævUF–ÖW")H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H ¢WFò¦Æ5÷F–ÖW"ÒæWrWFôÆæT6†ævUF–ÖW$6öçG&öÂ€¢.Éé¸ù’Ë
ÈJ»8«+Ò¸È«‹È¹Î«B"À¢.Ë
ÈJ»8«+ÒÉé¸ù’È¹ÎÉé«˜ÎÊxÉÙ‚¸È«‹È¹Î«NÉØBÈJNÊ	^ÙZ¸¸¸ºBåÆâ ¢.«	"ÊiŞ«‚²“¢»
Ùj^ÊxÈ¹Î¹;Ù¸B¸ÙBÉŠN¹é‚«‹¸ºNºkÂò«	"«	ÈhÂ‚Ò“¢¸ÙB»šºjÂË
ÈJ»8«+ÒòÊhÈ¹Ã¢Ê«BËjÊÊhÈ¹ÂÈ¹ÎÉéâ"À¢"ââö76WG2ööfg&öBö–6öå÷&öBçær"À¢F†—2“°¢Æ5÷F–ÖW"Óç6†÷tFW67&—F–öâ‚“°¢Æ—7BÓæFD—FVÒ†Æ5÷F–ÖW"“° ¢òò)H)HWFôÆæT6†ævU7VVB)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H ¢Æ—7BÓæFD—FVÒ†æWr&ÕfÇVT6öçG&öÄb‚$WFôÆæT6†ævU7VVB"À¢.Éé¸ù’Ë
ÈJ»8«+ÒËYÎÊÈhŞ¸øB"À¢.Éé¸ùœ+~»
Ùj^ÊxÈ¹Î¹;Ë
ÈJ»8«+ÒÙxÉª’ËYÎÊÈhŞ¸øB†¶Òö‚Éè^¸¸¸ºBâ«	"ÊiŞ«‚²“¢¸ÙB¸i.ÉØÈhŞ¸øNÉyÈIÎºxÂÉé¸ù’ò«	"«	ÈhÂ‚Ò“¢ÊÈhŞÉyÈIÎ¸øBÉé¸ù’â"À¢"ââö76WG2ööfg&öBö–6öå÷&öBçær"ÂÂÂÂÂSÂF†—2’“° ¢Æ—7BÓæFD—FVÒ††÷&—¦öçFÅöÆ–æR‚’“° ¢WFò¦F5öÖöFRÒæWr&ÕfÇVT6öçG&öÄb€¢$6'&÷DWFõGW&ä6öçG&öÂ"Â.Ë©¹ûò¸+N»˜BD2ºª¹9Â"À¢#¢¸Bò¢Ù¨ÎÊBÊÙj^»;NÊò#¢ÊÙj^»;NÊ¾Ù¨ÎÊN«	ÈhÒò3¢Ù¨ÎÊN«	ÈhŞºxÂÈ*ÎÉª’â"À¢"ââö76WG2ööfg&öBö–6öå÷&öBçær"ÂÂ2ÂÂÂÂF†—2“°¢F5öÖöFRÓç6†÷tFW67&—F–öâ‚“°¢Æ—7BÓæFD—FVÒ†F5öÖöFR“° ¢Æ—7BÓæFD—FVÒ†æWr&ÕfÇVT6öçG&öÄb€¢$6'&÷DWFõGW&å7VVB"Â.Ë©¹ûòD2Ù¨ÎÊNÈhŞ¸øB"À¢.Ù¨ÎÊB«ZÎ«BºªÙÎÈhŞ¸øB†¶Òö‚ÉÛNº›ºª¹9Â,+s>ÉyÈIÂÊÉª¹
¸¸¸ºBâ«	"ÊiŞ«‚²“¢¸ÙB»šº[N«(ÂÙ¨ÎÊBò«	"«	ÈhÂ‚Ò“¢¸ÙBºxîÉÛB«	ÈhÒâ"À¢"ââö76WG2ööfg&öBö–6öå÷7VVEöÆ–Ö—Bçær"Â3ÂcÂRÂÂ3ÂF†—2’“° ¢Æ—7BÓæFD—FVÒ†æWr&ÕfÇVT6öçG&öÄb€¢$6'&÷DWFõGW&äVæEF–ÖR"Â.Ë©¹ûòD2«	ÈhŞÈ¹ÎÊ	"À¢.Ù¨ÎÊN«	ÈhÒÊH»˜NÈ¹Î«BËH‚ÉÛNº›ºª¹9Â,+s>ÉyÈIÂÊÉª¹
¸¸¸ºBâ«	"ÊiŞ«‚²“¢¸ÙBÉÛÎËÒ«	ÈhÒÈ¹ÎÉéò«	"«	ÈhÂ‚Ò“¢Ù¨ÎÊNÉy««˜ÎÉ¸ÎÊ‚«	ÈhÒâ"À¢"ââö76WG2ööfg&öBö–6öå÷&öBçær"Â"Â"ÂÂÂbÂF†—2’“° ¢Æ—7BÓæFD—FVÒ††÷&—¦öçFÅöÆ–æR‚’“° ¢67&öÆÅf–Wr§67&öÆÆW"ÒæWr67&öÆÅf–Wr†Æ—7BÂF†—2“°¢67&öÆÆW"Óç6WEfW'F–6Å67&öÆÄ&%öÆ–7’…C£¥67&öÆÄ&$4æVVFVB“°¢Æ–÷WBÓæFEv–FvWB‡67&öÆÆW"“°§Ğ
#pragma once
#include <QButtonGroup>
#include <QFileSystemWatcher>
#include <QFrame>
#include <QLabel>
#include <QPushButton>
#include <QStackedWidget>
#include <QWidget>
#include <QStackedLayout>
#include "selfdrive/ui/qt/widgets/controls.h"

// ********** settings window + top-level panels **********
class SettingsWindow : public QFrame {
  Q_OBJECT
public:
  explicit SettingsWindow(QWidget *parent = 0);
  void setCurrentPanel(int index, const QString &param = "");
protected:
  void hideEvent(QHideEvent *event) override;
  void showEvent(QShowEvent *event) override;
signals:
  void closeSettings();
  void reviewTrainingGuide();
  void showDriverView();
  void expandToggleDescription(const QString &param);
private:
  QPushButton *sidebar_alert_widget;
  QWidget *sidebar_widget;
  QButtonGroup *nav_btns;
  QStackedWidget *panel_widget;
};

class DevicePanel : public ListWidget {
  Q_OBJECT
public:
  explicit DevicePanel(SettingsWindow *parent);
signals:
  void reviewTrainingGuide();
  void showDriverView();
  void closeSettings();
private slots:
  void poweroff();
  void reboot();
  void updateCalibDescription();
private:
  Params params;
};

class TogglesPanel : public ListWidget {
  Q_OBJECT
public:
  explicit TogglesPanel(SettingsWindow *parent);
  void showEvent(QShowEvent *event) override;
public slots:
  void expandToggleDescription(const QString &param);
private:
  Params params;
  std::map<std::string, ParamControl*> toggles;
  void updateToggles();
};

class SoftwarePanel : public ListWidget {
  Q_OBJECT
public:
  explicit SoftwarePanel(QWidget* parent = nullptr);
private:
  void showEvent(QShowEvent *event) override;
  void updateLabels();
  LabelControl *gitBranchLbl;
  LabelControl *gitCommitLbl;
  LabelControl *osVersionLbl;
  LabelControl *versionLbl;
  LabelControl *lastUpdateLbl;
  ButtonControl *updateBtn;
  Params params;
  QFileSystemWatcher *fs_watch;
};

class C2NetworkPanel: public QWidget {
  Q_OBJECT
public:
  explicit C2NetworkPanel(QWidget* parent = nullptr);
private:
  void showEvent(QShowEvent *event) override;
  QString getIPAddress();
  LabelControl *ipaddress;
};

class SelectCar : public QWidget {
  Q_OBJECT
public:
  explicit SelectCar(QWidget* parent = 0);
private:
signals:
  void backPress();
  void selectedCar();
};

class LateralControl : public QWidget {
  Q_OBJECT
public:
  explicit LateralControl(QWidget* parent = 0);
private:
signals:
  void backPress();
  void selected();
};

class CommunityPanel : public QWidget {
  Q_OBJECT
private:
  QStackedLayout* main_layout = nullptr;
  QWidget* homeScreen = nullptr;
  SelectCar* selectCar = nullptr;
  LateralControl* lateralControl = nullptr;
  QWidget* homeWidget;
public:
  explicit CommunityPanel(QWidget *parent = nullptr);
};

class VIPPanel : public QWidget {
  Q_OBJECT
public:
  explicit VIPPanel(QWidget* parent = nullptr);
};

class ChevronInfoControl : public AbstractControl {
  Q_OBJECT
public:
  ChevronInfoControl(const QString &title, const QString &desc,
                     const QString &icon, QWidget *parent = nullptr);
  void refresh();
private:
  QPushButton *buttons[5];
  Params params;
  const QStringList labels = {"Off", "Distance", "Speed", "Time", "All"};
};

// ── AutoLaneChangeTimer Control ─────────────────────────────────
class AutoLaneChangeTimerControl : public AbstractControl {
  Q_OBJECT
public:
  AutoLaneChangeTimerControl(const QString &title, const QString &desc,
                              const QString &icon, QWidget *parent = nullptr);
  void refresh();
private:
  QPushButton *buttons[6];
  Params params;
  // 인덱스 0~5 → 즉시 / 0.1s / 0.5s / 1.0s / 1.5s / 2.0s
  const QStringList labels = {"즉시", "0.1s", "0.5s", "1.0s", "1.5s", "2.0s"};
};

// ── DynamicLaneProfile Control ──────────────────────────────────
class DynamicLaneProfileControl : public AbstractControl {
  Q_OBJECT
public:
  DynamicLaneProfileControl(const QString &title, const QString &desc,
                             const QString &icon, QWidget *parent = nullptr);
  void refresh();
private:
  QPushButton *buttons[3];
  Params params;
  // 0: Lane only, 1: Lane less, 2: Auto
  const QStringList labels = {"Lane only", "Lane less", "Auto"};
};

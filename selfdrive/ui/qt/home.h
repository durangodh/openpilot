#pragma once

#include <QFrame>
#include <QLabel>
#include <QPushButton>
#include <QStackedLayout>
#include <QTimer>
#include <QWidget>
#include <QJsonObject>
#include <QMap>
#include <QCheckBox>

#include "selfdrive/ui/qt/offroad/driverview.h"
#include "selfdrive/ui/qt/body.h"
#include "selfdrive/ui/qt/onroad.h"
#include "selfdrive/ui/qt/sidebar.h"
#include "selfdrive/ui/qt/widgets/input.h"
#include "selfdrive/ui/qt/widgets/offroad_alerts.h"
#include "selfdrive/ui/ui.h"

// ── CarrotPilot Auto-Tuner (commit 9dd5e2c port) ─────────────────────────
// 사용 안내 다이얼로그
class AutoTunerGuideDialog : public QDialogBase {
  Q_OBJECT

public:
  explicit AutoTunerGuideDialog(const QString &html_content, QWidget *parent = nullptr);
  void showEvent(QShowEvent *event) override;
};

// 추천값 선택 적용 다이얼로그
class AutoTunerDialog : public QDialogBase {
  Q_OBJECT

public:
  QMap<QString, QCheckBox*> item_checkboxes;
  QJsonObject recommendations;
  explicit AutoTunerDialog(const QString &title_text, const QJsonObject &recs, QWidget *parent = nullptr);
  QJsonObject getSelectedItems();
};
// ─────────────────────────────────────────────────────────────────────────

class OffroadHome : public QFrame {
  Q_OBJECT

public:
  explicit OffroadHome(QWidget* parent = 0);

signals:
  void openSettings(int index = 0, const QString &param = "");

private:
  void showEvent(QShowEvent *event) override;
  void hideEvent(QHideEvent *event) override;
  void refresh();

  QTimer* timer;
  QLabel* date;
  QStackedLayout* center_layout;
  UpdateAlert *update_widget;
  OffroadAlert* alerts_widget;
  QPushButton* alert_notif;
  QPushButton* update_notif;
};

class HomeWindow : public QWidget {
  Q_OBJECT

public:
  explicit HomeWindow(QWidget* parent = 0);

signals:
  void openSettings(int index = 0, const QString &param = "");
  void closeSettings();

public slots:
  void offroadTransition(bool offroad);
  void showDriverView(bool show);
  void showSidebar(bool show);

protected:
  void mouseReleaseEvent(QMouseEvent* e) override;
  void mouseDoubleClickEvent(QMouseEvent* e) override;

private:
  Sidebar *sidebar;
  OffroadHome *home;
  OnroadWindow *onroad;
  BodyWindow *body;
  DriverViewWindow *driver_view;
  QStackedLayout *slayout;

private slots:
  void updateState(const UIState &s);
};

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

// Normalize lane directions from nMirror/Tmap. Lane direction values can be
// numbers, strings, or arrays depending on the APK version.
static int carrotLaneTypeValue(const QJsonValue &value) {
  if (value.isDouble()) return value.toInt();

  if (value.isArray()) {
    bool straight = false;
    bool left = false;
    bool right = false;
    bool uturn = false;
    int fallback = 0;
    bool has_value = false;
    for (const QJsonValue &item : value.toArray()) {
      const int type = carrotLaneTypeValue(item);
      has_value = true;
      fallback = type;
      straight |= type == 0 || type == 20 || type == 21;
      left |= type == 12 || type == 16 || type == 20;
      right |= type == 13 || type == 18 || type == 21;
      uturn |= type == 14;
    }
    if (uturn) return 14;
    if (straight && left) return 20;
    if (straight && right) return 21;
    if (left && right) return 22;
    return has_value ? fallback : 0;
  }

  const QString text = value.toString().trimmed().toLower();
  if (text.isEmpty()) return 0;

  bool numeric = false;
  const int numeric_type = text.toInt(&numeric);
  if (numeric) return numeric_type;

  const bool uturn = text.contains("uturn") || text.contains("u-turn") ||
                     text.contains(QString::fromUtf8("유턴"));
  const bool straight = text.contains("straight") || text.contains("through") ||
                        text.contains("forward") || text.contains(QString::fromUtf8("직진")) ||
                        text.contains(QString::fromUtf8("직좌")) ||
                        text.contains(QString::fromUtf8("좌직")) ||
                        text.contains(QString::fromUtf8("직우")) ||
                        text.contains(QString::fromUtf8("우직"));
  const bool left = text.contains("left") || text.contains(QString::fromUtf8("좌"));
  const bool right = text.contains("right") || text.contains(QString::fromUtf8("우"));

  if (uturn) return 14;
  if (straight && left) return 20;   // straight + left
  if (straight && right) return 21;  // straight + right
  if (left && right) return 22;      // left + right
  if (text.contains("slight_left") || text.contains("slight left")) return 16;
  if (text.contains("slight_right") || text.contains("slight right")) return 18;
  if (left) return 12;
  if (right) return 13;
  return 0;
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
      wrapper, {"lanes", "lane_info", "laneInfo", "items", "lane_items", "laneItems", "data"});
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
        type = carrotLaneTypeValue(carrotFirstJsonValue(
          item, {"turn_type", "turnType", "lane_type", "laneType",
                 "direction", "directions", "type"}));
        recommended = carrotJsonBool(item, {"recommended", "is_recommended",
                                             "isRecommended", "active", "is_active",
                                             "isActive", "selected", "usable"});
      } else if (lane.isBool()) {
        recommended = lane.toBool();
      } else if (lane.isDouble() || lane.isString() || lane.isArray()) {
        type = carrotLaneTypeValue(lane);
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
      types.push_back(i < direction_values.size() ? carrotLaneTypeValue(direction_values.at(i)) : 0);
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

  // nMirror가 제공하는 실제 Tmap 하단 차선 안내 이미지를 2Hz로 갱신한다.
  // JSON 방향값은 APK 버전마다 달라 이미지가 있으면 이를 우선 사용한다.
  const QString lane_image_path = "/dev/shm/carrot_navi_lane_bottom.png";
  const QFileInfo lane_image_info(lane_image_path);
  const qint64 lane_now = QDateTime::currentMSecsSinceEpoch();
  if (lane_image_info.exists()) {
    const qint64 lane_mtime = lane_image_info.lastModified().toMSecsSinceEpoch();
    if (lane_now - lane_mtime >= -5000 && lane_now - lane_mtime <= 5000) {
      if (lane_mtime != carrot_navi_lane_image_mtime || carrot_navi_lane_image.isNull()) {
        QPixmap next_lane_image;
        if (next_lane_image.load(lane_image_path)) {
          carrot_navi_lane_image = next_lane_image;
          carrot_navi_lane_scaled_image = QPixmap();
          carrot_navi_lane_scaled_width = 0;
          carrot_navi_lane_scaled_height = 0;
          carrot_navi_lane_image_mtime = lane_mtime;
        }
      }
    } else {
      carrot_navi_lane_image = QPixmap();
      carrot_navi_lane_scaled_image = QPixmap();
      carrot_navi_lane_scaled_width = 0;
      carrot_navi_lane_scaled_height = 0;
      carrot_navi_lane_image_mtime = 0;
    }
  } else {
    carrot_navi_lane_image = QPixmap();
    carrot_navi_lane_scaled_image = QPixmap();
    carrot_navi_lane_scaled_width = 0;
    carrot_navi_lane_scaled_height = 0;
    carrot_navi_lane_image_mtime = 0;
  }

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

enum class CarrotTurnDirection {
  STRAIGHT, LEFT, RIGHT, STRAIGHT_LEFT, STRAIGHT_RIGHT, LEFT_RIGHT,
  UTURN, SLIGHT_LEFT, SLIGHT_RIGHT, ARRIVE
};

enum class CarrotAtcKind { NONE, TURN, FORK, UTURN, ROTARY };

static bool carrotAtcTypeIn(int type, std::initializer_list<int> types) {
  return std::find(types.begin(), types.end(), type) != types.end();
}

[[maybe_unused]] static CarrotAtcKind carrotAtcKind(int type, const QString &text, int *direction) {
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
  if (type == 20) return CarrotTurnDirection::STRAIGHT_LEFT;
  if (type == 21) return CarrotTurnDirection::STRAIGHT_RIGHT;
  if (type == 22) return CarrotTurnDirection::LEFT_RIGHT;
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
  } else if (direction == CarrotTurnDirection::STRAIGHT_LEFT ||
             direction == CarrotTurnDirection::STRAIGHT_RIGHT ||
             direction == CarrotTurnDirection::LEFT_RIGHT) {
    const bool draw_straight = direction != CarrotTurnDirection::LEFT_RIGHT;
    const bool draw_left = direction == CarrotTurnDirection::STRAIGHT_LEFT ||
                           direction == CarrotTurnDirection::LEFT_RIGHT;
    const bool draw_right = direction == CarrotTurnDirection::STRAIGHT_RIGHT ||
                            direction == CarrotTurnDirection::LEFT_RIGHT;

    if (draw_straight) {
      path.moveTo(c.x(), c.y() + s);
      path.lineTo(c.x(), c.y() - s);
      path.moveTo(c.x() - s * 0.35, c.y() - s * 0.6);
      path.lineTo(c.x(), c.y() - s);
      path.lineTo(c.x() + s * 0.35, c.y() - s * 0.6);
    }
    if (draw_left) {
      path.moveTo(c.x(), c.y() + s * 0.25);
      path.lineTo(c.x(), c.y() - s * 0.15);
      path.lineTo(c.x() - s, c.y() - s * 0.15);
      path.moveTo(c.x() - s * 0.55, c.y() - s * 0.5);
      path.lineTo(c.x() - s, c.y() - s * 0.15);
      path.lineTo(c.x() - s * 0.55, c.y() + s * 0.2);
    }
    if (draw_right) {
      path.moveTo(c.x(), c.y() + s * 0.25);
      path.lineTo(c.x(), c.y() - s * 0.15);
      path.lineTo(c.x() + s, c.y() - s * 0.15);
      path.moveTo(c.x() + s * 0.55, c.y() - s * 0.5);
      path.lineTo(c.x() + s, c.y() - s * 0.15);
      path.lineTo(c.x() + s * 0.55, c.y() + s * 0.2);
    }
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
  const bool navi_fresh = carrot_navi_updated_at != 0 &&
                          wall_now - static_cast<qint64>(carrot_navi_updated_at) >= -5000 &&
                          wall_now - static_cast<qint64>(carrot_navi_updated_at) <= 35000;
  const bool route_active = carrot_navi_remain_distance > 0 &&
                            carrot_navi_remain_time > 0;
  if (!navi_fresh || !route_active) return;

  p.save();
  p.setRenderHint(QPainter::Antialiasing);
  p.setRenderHint(QPainter::TextAntialiasing);

  // c3-wip uses 1920x1080 coordinates. Scale uniformly so the compact
  // bottom-right guidance box keeps the original 790:300 aspect ratio.
  const qreal ui_scale = std::min(width() / 1920.0, height() / 1080.0);
  const int right_margin = qRound(10 * ui_scale);
  const int bottom_margin = qRound(10 * ui_scale);
  const int panel_w = qRound(790 * ui_scale);
  const int compact_h = qRound(300 * ui_scale);
  const QRect compact_box(width() - right_margin - panel_w,
                          height() - bottom_margin - compact_h,
                          panel_w, compact_h);

  int atc_direction = 0;
  const CarrotAtcKind atc_kind = carrotAtcKind(carrot_navi_turn_type,
                                               carrot_navi_instruction,
                                               &atc_direction);
  const qint64 guidance_age = wall_now -
      static_cast<qint64>(carrot_navi_guidance_updated_at);
  const bool atc_fresh = carrot_navi_guidance_updated_at != 0 &&
                         guidance_age >= -5000 && guidance_age <= 3000;
  const bool atc_available = carrot_atc_mode >= 1 && carrot_atc_mode <= 3 &&
                             atc_fresh && carrot_navi_distance >= 0 &&
                             atc_kind != CarrotAtcKind::NONE;

  // Keep c3-wip's road-position guidance while using vector artwork instead
  // of the removed PNG assets.
  if (atc_available && carrot_navi_distance < 1500) {
    const qreal distance_ratio = std::clamp(carrot_navi_distance / 1500.0, 0.0, 1.0);
    const int arrow_size = qRound(150 * ui_scale);
    const int arrow_center_y = qRound((520 - distance_ratio * 210.0) * ui_scale);
    const QRect road_arrow(width() / 2 - arrow_size / 2,
                           arrow_center_y - arrow_size / 2,
                           arrow_size, arrow_size);
    drawCarrotTurnArrow(p, road_arrow, carrot_navi_turn_type,
                        carrot_navi_instruction, QColor(255, 255, 255, 235),
                        std::max(7, qRound(16 * ui_scale)));
  }

  // The compact information box is always drawn first. A fresh map frame is
  // drawn over it later, matching c3-wip's persistent-box/map-overlay order.
  p.setPen(QPen(QColor(255, 255, 255, 220), std::max(1, qRound(2 * ui_scale))));
  p.setBrush(QColor(0, 0, 0, 150));
  p.drawRoundedRect(compact_box, qRound(30 * ui_scale), qRound(30 * ui_scale));

  QString title = carrot_navi_instruction;
  if (title.isEmpty()) title = carrot_navi_road;
  if (title.isEmpty()) title = QString::fromUtf8("경로 안내");
  configFont(p, "Open Sans", std::max(20, qRound(40 * ui_scale)), "Bold");
  p.setPen(Qt::white);
  p.drawText(QRect(compact_box.x() + qRound(20 * ui_scale),
                   compact_box.y() + qRound(5 * ui_scale),
                   compact_box.width() - qRound(40 * ui_scale),
                   qRound(58 * ui_scale)),
             Qt::AlignLeft | Qt::AlignVCenter,
             QFontMetrics(p.font()).elidedText(
                 title, Qt::ElideRight, compact_box.width() - qRound(40 * ui_scale)));

  const int turn_center_x = compact_box.x() + qRound(100 * ui_scale);
  const int turn_center_y = compact_box.y() + qRound(155 * ui_scale);
  const QRect turn_card(turn_center_x - qRound(80 * ui_scale),
                        turn_center_y - qRound(90 * ui_scale),
                        qRound(160 * ui_scale), qRound(230 * ui_scale));
  const bool atc_prepare = atc_available && carrot_navi_distance > 350;
  const QColor turn_fill = atc_available
      ? (atc_prepare ? QColor(0, 170, 80, 115) : QColor(0, 170, 80, 235))
      : QColor(20, 95, 60, 150);
  p.setPen(QPen(QColor(0, 0, 0, 220), std::max(1, qRound(ui_scale))));
  p.setBrush(turn_fill);
  p.drawRoundedRect(turn_card, qRound(15 * ui_scale), qRound(15 * ui_scale));

  const int turn_icon_size = qRound(130 * ui_scale);
  const QRect turn_icon(turn_center_x - turn_icon_size / 2,
                        turn_center_y - qRound(70 * ui_scale),
                        turn_icon_size, turn_icon_size);
  if (atc_kind != CarrotAtcKind::NONE) {
    drawCarrotTurnArrow(p, turn_icon, carrot_navi_turn_type,
                        carrot_navi_instruction, Qt::white,
                        std::max(6, qRound(13 * ui_scale)));
  } else {
    configFont(p, "Open Sans", std::max(18, qRound(32 * ui_scale)), "Bold");
    p.setPen(Qt::white);
    p.drawText(turn_icon, Qt::AlignCenter, QString::fromUtf8("안내"));
  }

  configFont(p, "Open Sans", std::max(19, qRound(40 * ui_scale)), "Bold");
  p.setPen(Qt::white);
  const QString turn_distance = carrotDistanceText(carrot_navi_distance);
  p.drawText(QRect(turn_card.x(), turn_card.bottom() - qRound(58 * ui_scale),
                   turn_card.width(), qRound(52 * ui_scale)),
             Qt::AlignCenter, turn_distance.isEmpty() ? QString("--") : turn_distance);

  const int info_x = compact_box.x() + qRound(190 * ui_scale);
  const int info_w = compact_box.right() - info_x - qRound(20 * ui_scale);
  const QDateTime eta = QDateTime::currentDateTime().addSecs(carrot_navi_remain_time);
  const QString eta_text = QString("ETA: %1MIN(%2)")
      .arg(carrot_navi_remain_time / 60.0, 0, 'f', 1)
      .arg(eta.toString("HH:mm"));
  configFont(p, "Open Sans", std::max(22, qRound(50 * ui_scale)), "Bold");
  p.setPen(Qt::white);
  p.drawText(QRect(info_x, compact_box.y() + qRound(80 * ui_scale),
                   info_w, qRound(62 * ui_scale)),
             Qt::AlignLeft | Qt::AlignVCenter, eta_text);

  const QString remain_text = carrotDistanceText(carrot_navi_remain_distance);
  p.drawText(QRect(info_x + qRound(120 * ui_scale),
                   compact_box.y() + qRound(132 * ui_scale),
                   info_w - qRound(120 * ui_scale), qRound(62 * ui_scale)),
             Qt::AlignLeft | Qt::AlignVCenter,
             remain_text.isEmpty() ? QString("--") : remain_text);

  QString road_text = carrot_navi_road;
  if (road_text.isEmpty()) road_text = carrot_navi_next_instruction;
  configFont(p, "Open Sans", std::max(18, qRound(40 * ui_scale)), "Bold");
  p.setPen(Qt::white);
  p.drawText(QRect(info_x, compact_box.y() + qRound(205 * ui_scale),
                   info_w, qRound(62 * ui_scale)),
             Qt::AlignLeft | Qt::AlignVCenter,
             QFontMetrics(p.font()).elidedText(road_text, Qt::ElideRight, info_w));

  // g_autoturn does not receive c3-wip's DISPLAY MAP state. The Tmap JPEG is
  // therefore the authoritative equivalent: poll at 5 Hz, show immediately
  // when a new frame arrives, and uncover the compact box 2.5 s after updates
  // stop. No header, footer, ETA strip, or lane-image layer is drawn here.
  static const QString map_path = "/dev/shm/carrot_navi_map.jpg";
  static QPixmap map_pixmap;
  static uint64_t map_checked_at = 0;
  static qint64 map_modified_at = 0;
  const uint64_t map_now = millis_since_boot();
  if (map_checked_at == 0 || map_now - map_checked_at >= 200) {
    map_checked_at = map_now;
    const QFileInfo map_info(map_path);
    if (!map_info.exists() || !map_info.isFile() || map_info.size() <= 0) {
      map_pixmap = QPixmap();
      map_modified_at = 0;
    } else {
      const qint64 modified_at = map_info.lastModified().toMSecsSinceEpoch();
      if (map_pixmap.isNull() || modified_at != map_modified_at) {
        QPixmap candidate;
        if (candidate.load(map_path, "JPEG")) {
          map_pixmap = candidate;
          map_modified_at = modified_at;
        } else {
          map_pixmap = QPixmap();
          map_modified_at = 0;
        }
      }
    }
  }

  const qint64 map_age = wall_now - map_modified_at;
  const bool map_fresh = !map_pixmap.isNull() && map_age >= 0 && map_age <= 2500;
  if (map_fresh) {
    const QRect map_panel(compact_box.x(), qRound(5 * ui_scale), compact_box.width(),
                          height() - qRound(20 * ui_scale));

    // Rebuild the crop only for a new Tmap frame or a geometry change. This
    // keeps map-only mode inexpensive on EON/C3 while paintEvent runs at UI Hz.
    static QImage map_scaled;
    static qint64 map_scaled_modified_at = -1;
    static QSize map_scaled_size;
    if (map_scaled_modified_at != map_modified_at ||
        map_scaled_size != map_panel.size()) {
      const qreal status_bar_h = std::min<qreal>(
          map_pixmap.height() / 8.0, map_pixmap.width() * 0.05);
      const QRectF content(0.0, status_bar_h, map_pixmap.width(),
                           map_pixmap.height() - status_bar_h);
      const qreal target_aspect =
          static_cast<qreal>(map_panel.width()) / map_panel.height();
      qreal source_w = content.width();
      qreal source_h = content.height();
      if (source_w / source_h > target_aspect) {
        source_w = source_h * target_aspect;
      } else {
        source_h = source_w / target_aspect;
      }
      const QRectF source_rect(content.center().x() - source_w / 2.0,
                               content.center().y() - source_h / 2.0,
                               source_w, source_h);
      const QPixmap scaled = map_pixmap.copy(source_rect.toRect()).scaled(
          map_panel.size(), Qt::IgnoreAspectRatio, Qt::SmoothTransformation);

      QImage rounded(map_panel.size(), QImage::Format_ARGB32_Premultiplied);
      rounded.fill(Qt::transparent);
      QPainter map_painter(&rounded);
      map_painter.setRenderHint(QPainter::Antialiasing);
      QPainterPath clip;
      clip.addRoundedRect(QRectF(QPointF(0, 0), map_panel.size()),
                          qRound(30 * ui_scale), qRound(30 * ui_scale));
      map_painter.setClipPath(clip);
      map_painter.drawPixmap(0, 0, scaled);
      map_painter.end();

      map_scaled = rounded;
      map_scaled_modified_at = map_modified_at;
      map_scaled_size = map_panel.size();
    }

    p.setPen(QPen(QColor(255, 255, 255, 220),
                  std::max(1, qRound(2 * ui_scale))));
    p.setBrush(QColor(0, 0, 0, 150));
    p.drawRoundedRect(map_panel, qRound(30 * ui_scale), qRound(30 * ui_scale));
    p.drawImage(map_panel.topLeft(), map_scaled);
  }

  p.restore();
}

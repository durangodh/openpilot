"""N16 -> legacy EON model fields, for offline compatibility inspection only.

No publishers or controller imports. Model arrays are not legacy ModelOutput
memory: N16 has one plan, three explicit leads and a different meta stride.
Reference: carrot-rust-dev rust/crates/modeld/{prediction,model_wire,geometry}.
"""
import numpy as np

from selfdrive.modeld.jetlink.client import SPEC, LinkError

TIME = np.square(np.arange(33, dtype=np.float64) / 32) * 10
DISTANCE = np.square(np.arange(33, dtype=np.float64) / 32) * 192


def exp(value):
  return np.exp(np.minimum(value, 11.0))


def sigmoid(value):
  return 1.0 / (1.0 + exp(-value))


def softmax(value):
  value = exp(value - np.max(value, axis=-1, keepdims=True))
  return value / value.sum(axis=-1, keepdims=True)


def line_times(plan):
  result = np.full(33, 10.0)
  result[0] = 0
  for x in range(1, 33):
    t = 0
    while t < 32 and plan[t + 1, 0] < DISTANCE[x]:
      t += 1
    if t == 32:
      break
    dx = plan[t + 1, 0] - plan[t, 0]
    if dx <= 1e-9:
      result[x] = TIME[t]
    else:
      fraction = np.clip((DISTANCE[x] - plan[t, 0]) / dx, 0, 1)
      result[x] = fraction * TIME[t + 1] + (1 - fraction) * TIME[t]
  return np.maximum.accumulate(result)


def xyzt(mean, times, std=None):
  result = dict(x=mean[:, 0].tolist(), y=mean[:, 1].tolist(), z=mean[:, 2].tolist(), t=times.tolist())
  if std is not None:
    for i, key in enumerate(('xStd', 'yStd', 'zStd')):
      result[key] = std[:, i].tolist()
  return result


class PredictionState:
  def __init__(self):
    self.frame = None
    self.brake5 = [0.0] * 5
    self.brake3 = [0.0] * 3

  def convert(self, output, frame_id):
    values = np.asarray(output, dtype=np.float64)
    if values.shape != (SPEC['output_shapes']['outputs'][1],) or not np.isfinite(values).all():
      raise LinkError('N16 출력 변환: 크기/수치 오류')
    if self.frame is None or ((frame_id - self.frame) & 0xffffffff) != 1:
      self.brake5, self.brake3 = [0.0] * 5, [0.0] * 3
    self.frame = frame_id

    def section(name):
      begin, end = SPEC['output_slices'][name]
      return values[begin:end]

    def normal(name, count):
      data = section(name)
      if len(data) != 2 * count:
        raise LinkError('N16 출력 변환: ' + name)
      return data[:count], exp(data[count:])

    plan, plan_std = normal('plan', 495)
    plan, plan_std = plan.reshape(33, 15), plan_std.reshape(33, 15)
    lanes, lane_std = normal('lane_lines', 264)
    edges, edge_std = normal('road_edges', 132)
    lead, lead_std = normal('lead', 72)
    pose, pose_std = normal('pose', 6)
    lanes, edges = lanes.reshape(4, 33, 2), edges.reshape(2, 33, 2)
    lead, lead_std = lead.reshape(3, 6, 4), lead_std.reshape(3, 6, 4)
    meta = sigmoid(section('meta'))
    self.brake5 = self.brake5[1:] + [float(meta[6])]
    self.brake3 = self.brake3[1:] + [float(meta[4])]
    hard_brake = all(p > threshold for p, threshold in zip(self.brake5, (.05, .05, .15, .15, .15))) and all(p > .7 for p in self.brake3)
    model = {}
    for key, begin in (('position', 0), ('velocity', 3), ('acceleration', 6), ('orientation', 9), ('orientationRate', 12)):
      model[key] = xyzt(plan[:, begin:begin + 3], TIME, plan_std[:, :3] if key == 'position' else None)
    times = line_times(plan)
    model['laneLines'] = [xyzt(np.column_stack((DISTANCE, lane)), times) for lane in lanes]
    model['roadEdges'] = [xyzt(np.column_stack((DISTANCE, edge)), times) for edge in edges]
    model['laneLineStds'] = lane_std[[0, 66, 132, 198]].tolist()
    model['laneLineProbs'] = sigmoid(section('lane_lines_prob'))[[1, 3, 5, 7]].tolist()
    model['roadEdgeStds'] = edge_std[[0, 66]].tolist()
    probs = sigmoid(section('lead_prob'))
    model['leadsV3'] = []
    for i in range(3):
      item = dict(t=[0., 2., 4., 6., 8., 10.], probTime=float(i * 2), prob=float(probs[i]))
      for column, key in enumerate(('x', 'y', 'v', 'a')):
        item[key] = lead[i, :, column].tolist()
        item[key + 'Std'] = lead_std[i, :, column].tolist()
      model['leadsV3'].append(item)
    disengage = dict(t=[2., 4., 6., 8., 10.])
    for start, key in ((1, 'gasDisengageProbs'), (2, 'brakeDisengageProbs'), (3, 'steerOverrideProbs'),
                       (4, 'brake3MetersPerSecondSquaredProbs'), (5, 'brake4MetersPerSecondSquaredProbs'),
                       (6, 'brake5MetersPerSecondSquaredProbs')):
      disengage[key] = meta[start:start + 25:6].tolist()
    model['meta'] = dict(engagedProb=float(meta[0]), desireState=softmax(section('desire_state')).tolist(),
                         desirePrediction=softmax(section('desire_pred').reshape(4, 8)).ravel().tolist(),
                         hardBrakePredicted=hard_brake, disengagePredictions=disengage)
    # Useful for odometry compatibility inspection, never sent to cameraOdometry.
    odometry = dict(trans=pose[:3].tolist(), rot=pose[3:].tolist(), transStd=pose_std[:3].tolist(), rotStd=pose_std[3:].tolist())
    summary = dict(path_y_2s=float(np.interp(2., TIME, plan[:, 1])),
                   speed_0s=float(plan[0, 3]), accel_0s=float(plan[0, 6]),
                   yaw_rate_2s=float(np.interp(2., TIME, plan[:, 14])),
                   lead_probability=float(probs[0]))
    return dict(model=model, odometry=odometry, summary=summary)

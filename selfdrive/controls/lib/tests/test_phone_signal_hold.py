import copy
import json

import pytest

from selfdrive.controls.lib.phone_signal_hold import MODEL_SHA, phone_red_hint, setting
from selfdrive.controls.lib.conditional_e2e import ConditionalE2EController
from selfdrive.controls.lib.tests.test_conditional_e2e import update


def packet():
  return {'protocol':1,'session':'a'*24,'frame_id':3,'model_sha256':MODEL_SHA,
          'observer_only':True,'ego_signal_confirmed':False,'received_unix_ms':100000,
          'roundtrip_ms':350,'selection':{'version':1,'status':'stable','ego_signal_confirmed':False,
          'detections':[{'label':'red','score':.74,'box':[.4,.2,.5,.25]}]}}


def read(tmp_path, data, now=100.1, enabled=True):
  config = tmp_path/'enabled'; result=tmp_path/'result'
  setting(enabled, str(config));result.write_text(json.dumps(data))
  return phone_red_hint(str(result),str(config),now=lambda:now)


def test_hint_requires_explicit_enable(tmp_path):
  assert not setting(path=str(tmp_path/'absent'))
  assert not read(tmp_path,packet(),enabled=False)
  assert read(tmp_path,packet())
  with pytest.raises(ValueError):setting('1',str(tmp_path/'enabled'))


@pytest.mark.parametrize('key,value',[('model_sha256','wrong'),('protocol',2),('session','bad'),
  ('frame_id',True),('frame_id',0),('observer_only',False),('ego_signal_confirmed',True),
  ('roundtrip_ms',float('nan')),('roundtrip_ms',-1),('roundtrip_ms',900),('received_unix_ms',float('inf'))])
def test_invalid_provenance_or_timing(tmp_path,key,value):
  d=packet();d[key]=value
  assert not read(tmp_path,d)


@pytest.mark.parametrize('status',['warming','conflict','ambiguous','none'])
def test_unstable_candidate_has_no_control_hint(tmp_path,status):
  d=packet();d['selection']['status']=status
  assert not read(tmp_path,d)


@pytest.mark.parametrize('label',['green','left','yellow','speed_sign'])
def test_no_permissive_or_yellow_authority(tmp_path,label):
  d=packet();d['selection']['detections'][0]['label']=label
  assert not read(tmp_path,d)


def test_red_plus_left_is_not_assumed_to_be_ego_red(tmp_path):
  d=packet();left=copy.deepcopy(d['selection']['detections'][0]);left['label']='left'
  d['selection']['detections'].append(left)
  assert not read(tmp_path,d)


@pytest.mark.parametrize('key,value',[('score',.59),('score',True),('score',float('nan')),
  ('box',[.5,.2,.4,.3]),('box',[0,0,1,float('inf')])])
def test_invalid_selected_detection(tmp_path,key,value):
  d=packet();d['selection']['detections'][0][key]=value
  assert not read(tmp_path,d)


def test_age_plus_roundtrip_is_bounded_and_future_is_rejected(tmp_path):
  assert not read(tmp_path,packet(),now=100.6)
  assert not read(tmp_path,packet(),now=99.9)


def test_missing_and_large_result_do_not_raise(tmp_path):
  config=tmp_path/'enabled';setting(True,str(config));result=tmp_path/'result'
  assert not phone_red_hint(str(result),str(config))
  result.write_text('x'*32769)
  assert not phone_red_hint(str(result),str(config))


def stopped():
  c=ConditionalE2EController(.05);c.stopping=True;c.stop_distance=0
  return c


def launch(c,**kw):
  args=dict(traffic_stop_mode=2,v_ego=0.,model_v0=0.,model_v_end=10.,model_x=200.,
            signal_phase='red',signal_distance=30.,phone_red_hint=True)
  args.update(kw)
  for _ in range(12):mode=update(c,**args)
  return mode


def test_apilot_corroborated_red_vetoes_model_only_launch():
  c=stopped();assert launch(c)=='acc'
  assert c.stopping and not c.prepare and c.phone_red_hold_active and c.stop_distance==0


@pytest.mark.parametrize('kw',[{'phone_red_hint':False},{'signal_phase':'green'},
  {'signal_phase':None},{'signal_distance':-1.},{'signal_distance':61.},{'right_blinker':True}])
def test_no_corroboration_returns_to_existing_start_confirmation(kw):
  c=stopped();launch(c,**kw)
  assert not c.phone_red_hold_active and c.prepare


def test_gas_keeps_manual_departure_priority():
  c=stopped();launch(c,gas_pressed=True)
  assert c.prepare and not c.phone_red_hold_active


def test_phone_red_cannot_create_a_stop_or_change_moving_acc():
  c=ConditionalE2EController(.05);launch(c,v_ego=10.,model_v0=10.)
  assert not c.stopping and not c.prepare and not c.phone_red_hold_active


def test_phone_does_not_rebrake_a_launch_already_in_progress():
  c=stopped();launch(c,phone_red_hint=False)
  assert c.prepare
  launch(c)
  assert c.prepare and not c.phone_red_hold_active


def test_drop_or_disable_requires_existing_confirmation_not_an_instant_launch():
  c=stopped();launch(c)
  update(c,traffic_stop_mode=2,v_ego=0.,model_v0=0.,model_v_end=10.,model_x=200.,
         signal_phase='red',signal_distance=30.,phone_red_hint=False)
  assert c.stopping and not c.prepare


@pytest.mark.parametrize('mode',[0,1,3])
def test_only_apilot_mode_gets_the_guard(mode):
  c=stopped();launch(c,traffic_stop_mode=mode)
  assert not c.phone_red_hold_active
  if mode==0:assert not c.stopping and not c.prepare


def test_near_lead_cannot_silently_bypass_active_red_guard():
  c=stopped();launch(c,lead_present=True,radar_lead_present=True,radar_lead_distance=5.,model_x=200.)
  assert c.stopping and c.phone_red_hold_active


def test_disengagement_resets_guard():
  c=stopped();launch(c);assert c.phone_red_hold_active
  update(c,available=False,phone_red_hint=True)
  assert not c.phone_red_hold_active and not c.stopping


def test_manual_experimental_mode_does_not_get_apilot_guard():
  c=stopped();launch(c,experimental_mode=True)
  assert not c.phone_red_hold_active


def test_corrupt_enable_file_is_disabled(tmp_path):
  config=tmp_path/'enabled';config.write_bytes(b'\xff')
  assert not setting(path=str(config))


def test_production_planner_forwards_hint_only_in_existing_standstill(monkeypatch):
  import ast
  import textwrap
  from pathlib import Path
  from types import SimpleNamespace as NS
  source=(Path(__file__).resolve().parents[1]/'longitudinal_planner.py').read_text()
  tree=ast.parse(source);cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='LongitudinalPlanner')
  node=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='update_auto_e2e_mode')
  calls=[]
  namespace={'phone_red_hint':lambda:calls.append(True) or True,
             'clip':lambda v,a,b:max(a,min(b,v)),
             'adjust_stop_distance_for_decel':lambda d,*args:d,'E2E_VISION_LEAD_DISTANCE':90}
  exec(compile(textwrap.dedent(ast.get_source_segment(source,node)),'planner-method','exec'),namespace)
  fn=namespace['update_auto_e2e_mode']
  controller=stopped()
  obj=NS(navigation_route=NS(update=lambda:{'signal':{'phase':'red','distance':30.,'remaining':20.}}),
         auto_e2e_enabled=True,experimental_mode_enabled=False,traffic_stop_mode=2,
         conditional_e2e=controller,mpc=NS(),traffic_stop_accel_factor=.8,traffic_stop_distance_adjust=4.)
  car=NS(vEgo=0.,steeringAngleDeg=0.,gasPressed=False,brakePressed=False,rightBlinker=False)
  lead=NS(status=False,radar=False,dRel=0.)
  radar=NS(leadOne=lead,leadTwo=lead)
  model=NS(position=NS(x=[200.]*33,y=[0.]*33),velocity=NS(x=[10.]*33))
  for _ in range(12):assert fn(obj,car,radar,model,True,3,1.)=='acc'
  assert calls and controller.phone_red_hold_active and obj.mpc.traffic_stop_active
  calls.clear();obj.traffic_stop_mode=0
  fn(obj,car,radar,model,True,3,1.)
  assert not calls and not obj.mpc.traffic_stop_active


def test_web_switch_uses_authenticated_same_origin_action(monkeypatch):
  import ast
  import io
  import subprocess
  import textwrap
  from pathlib import Path
  from types import SimpleNamespace as NS
  import selfdrive.controls.lib.phone_signal_hold as config
  calls=[]
  monkeypatch.setattr(config,'setting',lambda enabled:calls.append(enabled) or enabled)
  source=(Path(__file__).resolve().parents[3]/'eon_web.py').read_text()
  tree=ast.parse(source);cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Handler')
  node=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='do_POST')
  namespace={'json':json,'subprocess':subprocess}
  exec(compile(textwrap.dedent(ast.get_source_segment(source,node)),'web-post','exec'),namespace)
  def post(value, origin='http://eon:7000', authenticated=True):
    body=json.dumps({'enabled':value}).encode();replies=[]
    h=NS(path='/api/signal-hold',authorized=lambda:authenticated,
         headers={'Host':'eon:7000','Origin':origin,'X-EON-Action':'set-signal-hold','Content-Length':str(len(body))},
         rfile=io.BytesIO(body),send_json=lambda body,status:replies.append((status,body)))
    namespace['do_POST'](h)
    return replies
  assert post(True)==[(200,{'enabled':True})] and calls==[True]
  assert post('yes')[0][0]==400 and calls==[True]
  assert post(False,origin='http://elsewhere')[0][0]==403 and calls==[True]
  assert post(False,authenticated=False)==[] and calls==[True]

import copy,unittest
from flow_runtime.spice import validate_protocol
from flow_runtime.state import Fault

class ProtocolShapeTests(unittest.TestCase):
 def test_nested_wrong_types_are_proposal_faults(self):
  base={'temperature_C':25,'device_nodes':{},'components':[{'kind':'V','name':'VD','nodes':['P','0'],'value':{'dc':0}}],'models':{},'analysis':{'kind':'dc','source':'VD','start':0,'stop':1,'step':.1},'measurement':{'mode':'sample','signal':'v(P)','at':.5},'checks':[]}
  variants=[('models',[]),('models',{'DM':[]}),('analysis',[]),('components',{}),('components',[None]),('checks',[None]),('device_nodes',[]),('device_nodes',{'P':2}),('measurement',[])]
  for field,value in variants:
   with self.subTest(field=field,value=value):
    p=copy.deepcopy(base);p[field]=value
    with self.assertRaises(Fault) as caught:validate_protocol(p,{'ports':['P']})
    self.assertEqual(caught.exception.kind,'proposal')
  validate_protocol(base,{'ports':['P']})

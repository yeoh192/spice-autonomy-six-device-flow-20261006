"""Independent physical oracles for each AC quantity, at two excitation amplitudes."""
import math
from .ac_measurements import MODES


def protocols(mode):
    if mode not in MODES:
        raise ValueError(mode)
    out=[]
    for factor in (1,2):
        f=1000; r=1000; c=factor*1e-6; l=factor*1e-3
        p={"device_nodes":{},"temperature_C":25,"checks":[],"analysis":{"kind":"ac","frequency_Hz":f},
           "components":[{"kind":"V","name":"VCAL","nodes":["P","0"],"value":{"dc":0,"ac":factor*.3}}],
           "measurement":{"mode":mode,"signal":"v(p)","denominator":"i(vcal)","sign":-1}}
        def element(kind,name,n1,n2,value):
            p["components"].append({"kind":kind,"name":name,"nodes":[n1,n2],"value":value})
        if mode in ("ac_current_ratio","ac_reactance_ratio"):
            p["components"]=[{"kind":"I","name":"IA","nodes":["0","P"],"value":{"dc":0,"ac":factor*.3}}, {"kind":"I","name":"IB","nodes":["0","Q"],"value":{"dc":0,"ac":factor*.3}}]
            if mode=="ac_reactance_ratio":
                element("L","LA","P","0",factor*1e-3);element("L","LB","Q","0",1e-3)
                p["measurement"].update(signal="v(p)",denominator="v(q)",sign=1)
            else:
                element("R","RA","P","0",1000);element("R","RB","Q","0",1000)
                p["components"][0]["value"]["ac"]=factor*.3
                p["components"][1]["value"]["ac"]=.3
                p["measurement"].update(signal="i(ia)",denominator="i(ib)",sign=1)
            expected=factor
        elif mode in ("ac_gain_magnitude","ac_gain_db","ac_phase"):
            element("R","RA","P","O",r); element("R","RB","O","0",factor*r)
            p["measurement"].update(signal="v(o)",denominator="v(p)",sign=1)
            g=factor/(1+factor)
            expected=20*math.log10(g) if mode=="ac_gain_db" else 0 if mode=="ac_phase" else g
        elif mode == "ac_inductance":
            element("L","LCAL","P","0",l);expected=l
        elif mode == "ac_parallel_capacitance":
            element("R","RCAL","P","0",r);element("C","CCAL","P","0",c);expected=c
        else:
            element("R","RCAL","P","M",r);element("C","CCAL","M","0",c)
            expected={"ac_resistance":r,"ac_impedance_magnitude":math.hypot(r,1/(2*math.pi*f*c)),
                      "ac_series_capacitance":c,"ac_dissipation_factor":2*math.pi*f*r*c}[mode]
        out.append((p,expected))
    return out

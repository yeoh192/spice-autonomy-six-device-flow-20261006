"""Plot only recorded comparisons; do not infer missing handbook data."""
import csv,math,re

def generate(report,out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    folder=out/'plots';folder.mkdir(exist_ok=True);items=[]
    for index,r in enumerate(report.get('results',[])):
        rows=r.get('comparison') or []
        if not rows:continue
        if not all(all(isinstance(p.get(k),(int,float)) and math.isfinite(p[k]) for k in ('x','reference_y','simulated_y')) for p in rows):continue
        rows=sorted(rows,key=lambda p:p['x']);name=str(index)+'_'+re.sub(r'[^A-Za-z0-9_.-]','_',r['test']);unit=r.get('unit','')
        with (folder/(name+'.csv')).open('w',newline='',encoding='utf-8-sig') as f:
            w=csv.DictWriter(f,fieldnames=['x','reference_y','simulated_y']);w.writeheader();w.writerows({k:p[k] for k in w.fieldnames} for p in rows)
        x=[p['x'] for p in rows];ref=[p['reference_y'] for p in rows];sim=[p['simulated_y'] for p in rows]
        fig,axes=plt.subplots(1,2,figsize=(11,4),layout='constrained')
        axes[0].plot(x,sim,color='#226e50',label='Simulation (at reference x)',linewidth=1.8)
        axes[0].scatter(x,ref,s=12,color='#d27c39',label='Reference',alpha=.65)
        axes[0].set_xlabel('Reference x (original axis)');axes[0].set_ylabel(unit);axes[0].legend(fontsize=8)
        axes[1].scatter(ref,sim,s=13,color='#226e50',alpha=.7)
        lo=min(ref+sim);hi=max(ref+sim)
        if lo==hi:lo-=max(abs(lo)*.05,1e-9);hi+=max(abs(hi)*.05,1e-9)
        axes[1].plot([lo,hi],[lo,hi],'--',color='#acb8b0',label='y = x');axes[1].set_xlabel('Reference ('+unit+')');axes[1].set_ylabel('Simulation ('+unit+')');axes[1].legend(fontsize=8)
        for ax in axes:ax.grid(alpha=.18);ax.ticklabel_format(style='sci',scilimits=(-3,4),axis='both')
        fig.suptitle(r['test']+' | '+str(r.get('acceptance','pending')),fontsize=11)
        for ext in ('png','svg'):fig.savefig(folder/(name+'.'+ext),dpi=160)
        plt.close(fig)
        items.append({'test':r['test'],'acceptance':r.get('acceptance'),'unit':unit,'points':len(rows),'png':'plots/'+name+'.png','svg':'plots/'+name+'.svg','csv':'plots/'+name+'.csv'})
    return items

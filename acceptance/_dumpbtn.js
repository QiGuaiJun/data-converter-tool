const { chromium } = require('playwright');
const BASE='http://127.0.0.1:51979';
(async()=>{
  const b=await chromium.launch(); const p=await b.newPage({viewport:{width:1500,height:950}});
  for (const u of ['/export.html','/index.html']) {
    await p.goto(BASE+u,{waitUntil:'networkidle'}); await p.waitForTimeout(2500);
    const info = await p.evaluate(()=>{
      const btns=[...document.querySelectorAll('button,a.button,[role=button]')].map(e=>{
        const r=e.getBoundingClientRect();
        return {id:e.id||'', text:(e.innerText||'').replace(/\s+/g,' ').trim().slice(0,16), vis: !!(r.width&&r.height)};
      }).filter(x=>x.vis);
      return {list:btns.slice(0,20), total:btns.length};
    });
    console.log('\n==',u,'可见按钮',info.total); console.log(JSON.stringify(info.list));
  }
  await b.close();
})();

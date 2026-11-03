/** One default workspace. Explicit old full-form links remain available for recovery. */
export function unifiedEntry(search='',pathname='/paramedic/'){
  const query=new URLSearchParams(search);
  if(query.get('view')==='full'){
    return {kind:'legacy',url:'./legacy.html?'+query.toString()};
  }
  query.delete('view');query.set('flow','field');
  const canonical=pathname.endsWith('/quick-test.html')||pathname.endsWith('/index.html')
    ? pathname.slice(0,pathname.lastIndexOf('/')+1):pathname;
  return {kind:'workspace',url:canonical+'?'+query.toString()};
}
if(typeof document!=='undefined'&&typeof window!=='undefined'){
  const next=unifiedEntry(location.search,location.pathname);
  if(next.kind==='legacy')location.replace(next.url);
  else{
    document.documentElement.dataset.unifiedWorkspace='true';
    if(location.pathname+location.search!==next.url)window.history.replaceState(null,'',next.url+location.hash);
    await import('./quick-test.js');
  }
}

var Q='', DOM='', TYP='', CAT='';
var results=[], totalCount=0, offset=0, PAGE=50, selId=null, selIdx=-1;
var apiReady=false, apiAttempts=0, searchTimer;
var dbg=document.getElementById('status');

function log(s){try{dbg.textContent=s}catch(e){}}

function waitApi(){
    if(typeof pywebview!=='undefined'&&pywebview.api){
        apiReady=true;
        log('API ready');
        loadFilters();
    }else if(apiAttempts<30){
        apiAttempts++;
        log('Waiting API... ('+apiAttempts+')');
        setTimeout(waitApi,200);
    }else{
        log('API timeout');
        document.getElementById('rlist').innerHTML='<div class="empty"><p>Connection failed</p></div>';
    }
}
setTimeout(waitApi,100);

function loadFilters(){
    if(!apiReady)return;
    pywebview.api.get_facets('').then(function(resp){
        var data=JSON.parse(resp);
        if(data.error){log(data.error);return}
        populateSel('fdomain',data.domains||[],{gi:'Genshin',hsr:'Star Rail'});
        populateSel('ftype',data.types||[]);
        populateSel('fcat',data.categories||[]);
        log('Filters loaded');
        doSearch();
    }).catch(function(e){log('Filter error: '+e.message)});
}

function populateSel(id,items,labels){
    var sel=document.getElementById(id);
    for(var i=0;i<items.length;i++){
        var v=items[i];
        var o=document.createElement('option');
        o.value=v;
        o.textContent=labels&&labels[v]?labels[v]:v;
        sel.appendChild(o);
    }
}

function doSearch(){
    if(!apiReady){log('API not ready');return}
    offset=0; selIdx=-1;
    pywebview.api.search(Q,DOM,TYP,CAT,PAGE,0).then(function(resp){
        var data=JSON.parse(resp);
        results=data.results||[];
        totalCount=data.total||0;
        document.getElementById('rcount').textContent=totalCount;
        renderList(results);
        log('Found '+totalCount+' results');
    }).catch(function(e){log('Search error: '+e.message)});
}

function loadMore(){
    offset+=PAGE;
    if(offset>=totalCount)return;
    pywebview.api.search(Q,DOM,TYP,CAT,PAGE,offset).then(function(resp){
        var data=JSON.parse(resp);
        var more=data.results||[];
        totalCount=data.total||0;
        results=results.concat(more);
        renderList(results);
        document.getElementById('rcount').textContent=totalCount;
    }).catch(function(e){log('Load error: '+e.message)});
}

function renderList(items){
    var el=document.getElementById('rlist');
    if(items.length===0){
        el.innerHTML='<div class="empty"><p>No results</p></div>';
    }else{
        var html='';
        for(var i=0;i<items.length;i++){
            var d=items[i];
            var cls=d.id===selId?' active':'';
            var dom=d.domain==='gi'?'Genshin':'Star Rail';
            var snip=d.snippet?'<div class="card-snippet">'+d.snippet+'</div>':'';
            html+='<div class="card'+cls+'" data-id="'+d.id+'" onclick="selectDoc(\''+d.id+'\')">'+
                '<div class="card-name">'+esc(d.name)+'</div>'+
                '<div class="card-meta">'+
                '<span class="badge badge-'+d.domain+'">'+dom+'</span>'+
                '<span class="badge badge-type">'+esc(d.doc_type||'')+'</span>'+
                '<span class="badge badge-type">'+esc(d.category||'')+'</span>'+
                '</div>'+snip+'</div>';
        }
        el.innerHTML=html;
    }
    document.getElementById('loadmore').style.display=totalCount>results.length?'block':'none';
}

function selectDoc(id){
    selId=id;
    var cards=document.querySelectorAll('.card');
    for(var i=0;i<cards.length;i++)cards[i].classList.toggle('active',cards[i].dataset.id===id);
    pywebview.api.get_doc(id).then(function(resp){
        var doc=JSON.parse(resp);
        if(doc.error)return;
        var html='<h2>'+esc(doc.name)+'</h2>';
        html+='<div class="detail-tags">';
        html+='<span class="badge badge-'+(doc.domain||'gi')+'">'+(doc.domain==='gi'?'Genshin':'Star Rail')+'</span>';
        if(doc.doc_type)html+='<span class="badge badge-type">'+esc(doc.doc_type)+'</span>';
        if(doc.category)html+='<span class="badge badge-type">'+esc(doc.category)+'</span>';
        html+='</div>';
        html+='<div class="detail-content">'+md2html(doc.content||'')+'</div>';
        document.getElementById('detail').innerHTML=html;
        document.getElementById('detail').scrollTop=0;
    }).catch(function(e){});
}

function md2html(md){
    if(!md)return'';
    var h=md;
    h=h.replace(/&/g,'&amp;');h=h.replace(/</g,'&lt;');h=h.replace(/>/g,'&gt;');
    h=h.replace(/^### (.+)$/gm,'<h3>$1</h3>');
    h=h.replace(/^## (.+)$/gm,'<h2>$1</h2>');
    h=h.replace(/^# (.+)$/gm,'<h1>$1</h1>');
    h=h.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
    h=h.replace(/\*(.+?)\*/g,'<em>$1</em>');
    h=h.replace(/```([\s\S]*?)```/g,'<pre><code>$1</code></pre>');
    h=h.replace(/`(.+?)`/g,'<code>$1</code>');
    h=h.replace(/^> (.+)$/gm,'<blockquote>$1</blockquote>');
    h=h.replace(/^---$/gm,'<hr>');
    h=h.replace(/^- (.+)$/gm,'<li>$1</li>');
    h=h.replace(/^(\d+)\. (.+)$/gm,'<li>$2</li>');
    h=h.replace(/\n\n/g,'</p><p>');
    h=h.replace(/\n/g,'<br>');
    if(h.indexOf('<')!==0)h='<p>'+h+'</p>';
    h=h.replace(/<p><\/p>/g,'');
    return h;
}
function esc(s){if(!s)return'';var d=document.createElement('div');d.textContent=s;return d.innerHTML}

document.addEventListener('keydown',function(e){
    if(e.key==='/'&&document.activeElement!==document.getElementById('q')){
        e.preventDefault();document.getElementById('q').focus();
    }
    if(e.key==='Escape'){document.getElementById('q').value='';Q='';doSearch()}
    if(e.key==='ArrowDown'&&results.length>0){
        e.preventDefault();selIdx=Math.min(selIdx+1,results.length-1);
        selectDoc(results[selIdx].id);
        var cd=document.querySelectorAll('.card')[selIdx];
        if(cd)cd.scrollIntoView({block:'nearest',behavior:'smooth'});
    }
    if(e.key==='ArrowUp'&&results.length>0){
        e.preventDefault();selIdx=Math.max(selIdx-1,0);
        selectDoc(results[selIdx].id);
        var cd=document.querySelectorAll('.card')[selIdx];
        if(cd)cd.scrollIntoView({block:'nearest',behavior:'smooth'});
    }
});

document.getElementById('q').addEventListener('input',function(){
    Q=this.value;
    clearTimeout(searchTimer);
    searchTimer=setTimeout(doSearch,300);
});
document.getElementById('fdomain').addEventListener('change',function(){DOM=this.value;doSearch()});
document.getElementById('ftype').addEventListener('change',function(){TYP=this.value;doSearch()});
document.getElementById('fcat').addEventListener('change',function(){CAT=this.value;doSearch()});

function toast(msg){
    var t=document.createElement('div');t.className='toast';t.textContent=msg;
    document.body.appendChild(t);
    setTimeout(function(){if(t.parentNode)t.remove()},2000);
}

document.addEventListener('keydown',function(e){
    if((e.ctrlKey||e.metaKey)&&e.key==='c'&&selId&&!window.getSelection().toString()){
        e.preventDefault();
        pywebview.api.get_doc(selId).then(function(r){
            var d=JSON.parse(r);
            navigator.clipboard.writeText('# '+d.name+'\n\n'+d.content).then(function(){toast('Copied')}).catch(function(){});
        });
    }
});

log('Init done');

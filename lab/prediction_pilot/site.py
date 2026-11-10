"""Static, matched two-step desk. No API, root registry, history, Skills or outcome access."""
import argparse
import html
import json
import time
from pathlib import Path
from urllib.parse import parse_qs
from fastapi import FastAPI,Request
from fastapi.responses import HTMLResponse,RedirectResponse
from .protocol import CONDITIONS,FORMS,FOOTER,TASKS,WELCOME

def make_app(condition,phase,directory):
    if condition not in CONDITIONS or phase not in TASKS:raise ValueError('Unknown desk')
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    log=directory/'website-events.jsonl';log.touch(exist_ok=False);events=[];answers=set();registered=False;closed=False;began=time.monotonic()
    task=TASKS[phase];pages=[]
    app=FastAPI()
    def event(request,status,**fields):
        row={'seq':len(events)+1,'elapsed_s':round(time.monotonic()-began,4),'method':request.method,
          'path':request.url.path if request.url.path in {'/start','/intake','/step/1','/step/2','/answer/1','/answer/2','/document','/exit'} else '/other',
          'status':status,**fields};events.append(row)
        with log.open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    def response(request,body,status=200,**fields):
        event(request,status,**fields);return HTMLResponse(body,status_code=status,headers={'Cache-Control':'no-store'})
    def redirect(request,path,**fields):
        event(request,303,**fields);return RedirectResponse(path,status_code=303)
    def remember(path,body):
        if not any(x['path']==path for x in pages):
            pages.append({'path':path,'html':body});(directory/'issued-pages.json').write_text(json.dumps(pages,ensure_ascii=False,indent=2))
        return body
    async def form(request,allowed):
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>4096:raise ValueError('Body too large')
        if request.headers.get('content-type','').split(';')[0]!='application/x-www-form-urlencoded':raise ValueError('Form encoding')
        values=parse_qs(raw.decode(),keep_blank_values=True,strict_parsing=True,max_num_fields=4)
        if not set(values)<=allowed or any(len(v)!=1 for v in values.values()):raise ValueError('Unknown/duplicate fields')
        return {k:v[0].strip() for k,v in values.items()}
    @app.api_route('/{rest:path}',methods=['GET','POST'])
    async def route(rest,request:Request):
        nonlocal registered,closed
        path='/'+rest
        if closed:return response(request,'Desk visit ended.',410)
        if path=='/start' and request.method=='GET':
            body=('<h1>Document workbench</h1><p>We help organize the information needed to complete your current task.</p>'
              '<p>'+html.escape(WELCOME[condition])+'</p>'
              '<form action="/intake" method="post"><label>Brief current task goal<input name="goal" maxlength="160" required></label>'
              '<button>Register goal and continue</button></form><a href="/document">Read the source directly</a>'
              '<form action="/exit" method="post"><button>Leave</button></form><p>'+FOOTER+'</p>')
            return response(request,remember(path,body))
        if path=='/intake' and request.method=='POST':
            try:
                data=await form(request,{'goal'});goal=data['goal']
                if not 1<=len(goal)<=160:raise ValueError('Goal length')
            except (KeyError,ValueError,UnicodeError):return response(request,'Invalid goal form.',422)
            registered=True;return redirect(request,'/step/1',goal=goal)
        if path in ('/step/1','/step/2') and request.method=='GET':
            n=int(path[-1]);q=FORMS[condition][n-1]
            body='<h1>Task desk: step '+str(n)+' of 2</h1><p>'+html.escape(q['question'])+'</p>'
            body+=f'<form action="/answer/{n}" method="post">'
            for option in q['options']:
                body+=f'<label><input type="radio" name="choice" value="{option["id"]}" required>{html.escape(option["text"])}</label><br>'
            body+='<label><input type="radio" name="choice" value="decline">Prefer not to answer</label>'
            body+='<label>Short task-level note (optional)<textarea name="note" maxlength="500"></textarea></label><button>Continue</button></form>'
            body+='<a href="/document">Read directly without this step</a><p>'+FOOTER+'</p>'
            return response(request,remember(path,body))
        if path in ('/answer/1','/answer/2') and request.method=='POST':
            n=int(path[-1]);q=FORMS[condition][n-1]
            try:
                data=await form(request,{'choice','note'});choice=data['choice'];note=data.get('note','')
                if choice not in {x['id'] for x in q['options']}|{'decline'} or len(note)>500 or n in answers:raise ValueError('Invalid answer')
            except (KeyError,ValueError,UnicodeError):return response(request,'Invalid or duplicate answer.',422)
            answers.add(n)
            return redirect(request,'/step/2' if n==1 else '/document',round=n,choice=choice,note=note)
        if path=='/document' and request.method=='GET':
            body='<h1>'+html.escape(task['title'])+'</h1><p>'+html.escape(task['body'])+'</p><p>Source: synthetic '+html.escape(task['title'])+'.</p>'
            return response(request,remember(path,body))
        if path=='/exit' and request.method=='POST':closed=True;return response(request,'Visit ended.',action='exit')
        return response(request,'Not found.',404)
    (directory/'issued-pages.json').write_text('[]')
    return app

def main():
    import uvicorn
    p=argparse.ArgumentParser();p.add_argument('--condition',choices=CONDITIONS,required=True);p.add_argument('--phase',choices=TASKS,required=True)
    p.add_argument('--directory',type=Path,required=True);a=p.parse_args()
    uvicorn.run(make_app(a.condition,a.phase,a.directory),uds='site.sock',access_log=False,log_level='warning')

if __name__=='__main__':main()

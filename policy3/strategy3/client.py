"""Serial four-endpoint HTTP protocol with identical-payload retries."""
import json
import math
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler


class Client:
    def __init__(self,base_url,robot_id,cfg,log):
        if not robot_id or len(robot_id.encode('utf-8'))>64:
            raise ValueError('Invalid robot id')
        self.base_url=base_url.rstrip('/')
        self.robot_id=robot_id
        self.cfg=cfg
        self.log=log
        self.opener=build_opener(ProxyHandler({}))
        self.pending=None
        self.deadline=math.inf

    def call(self,path,action=None):
        if self.pending is not None:
            raise RuntimeError('Unresolved request: no new request ID may be issued')
        rid=uuid.uuid4().hex
        payload=dict(arena_id='default',robot_id=self.robot_id,request_id=rid)
        if action is not None:
            payload.update(position=dict(x=action.point[0],y=action.point[1]),channel=action.channel)
        body=json.dumps(payload,allow_nan=False,separators=(',',':')).encode('utf-8')
        self.pending=(path,rid,body)
        self.log('request',path=path,request=payload)
        for attempt in range(self.cfg.http_retries+1):
            remaining=self.deadline-time.monotonic()
            if remaining<=0:
                raise TimeoutError('HTTP operation deadline reached')
            try:
                req=Request(self.base_url+path,data=body,headers={'Content-Type':'application/json'},method='POST')
                with self.opener.open(req,timeout=min(self.cfg.http_timeout_s,remaining)) as response:
                    result=json.load(response)
                self.log('response',path=path,request_id=rid,response=result,attempt=attempt)
                self.pending=None
                if result.get('accepted') is not True:
                    raise RuntimeError('Simulator rejected the request')
                return rid,result
            except HTTPError as exc:
                if exc.code<500:
                    self.pending=None
                    raise
                self.log('http_retry',request_id=rid,attempt=attempt,error=str(exc))
            except (URLError,TimeoutError,ConnectionError,OSError) as exc:
                self.log('http_retry',request_id=rid,attempt=attempt,error=str(exc))
            if attempt<self.cfg.http_retries:
                time.sleep(min(.05*(attempt+1),max(0,self.deadline-time.monotonic())))
        raise RuntimeError('HTTP response uncertain; exact pending payload retained')

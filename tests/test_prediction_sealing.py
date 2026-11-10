import json
import pytest
from prediction_pilot.sealing import seal_predictions


def test_resume_preserves_timestamp_and_bytes(tmp_path,monkeypatch):
    path=tmp_path/'seal.json';pred={'root':{'result':{'p':.2}}}
    monkeypatch.setattr('prediction_pilot.sealing.time.time',lambda:100.)
    first=seal_predictions(path,pred,{'root':['information']},{'file':0.})
    raw=path.read_bytes()
    monkeypatch.setattr('prediction_pilot.sealing.time.time',lambda:900.)
    assert seal_predictions(path,pred,{'root':['information']},{'file':0.})==first
    assert path.read_bytes()==raw and first['at_unix']==100.


@pytest.mark.parametrize('changed',('predictions','orders','base_rates'))
def test_resume_rejects_changed_inputs_without_replacing_seal(tmp_path,changed):
    path=tmp_path/'seal.json';args={'predictions':{},'orders':{},'base_rates':{}}
    seal_predictions(path,**args);raw=path.read_bytes();args[changed]={'changed':True}
    with pytest.raises(RuntimeError):seal_predictions(path,**args)
    assert path.read_bytes()==raw


@pytest.mark.parametrize('stamp',(None,True,-1,float('nan')))
def test_corrupt_seal_fails_closed(tmp_path,stamp):
    path=tmp_path/'seal.json'
    path.write_text(json.dumps({'at_unix':stamp,'predictions':{},'orders':{},'base_rates':{}}))
    raw=path.read_bytes()
    with pytest.raises(RuntimeError):seal_predictions(path,{},{},{})
    assert path.read_bytes()==raw

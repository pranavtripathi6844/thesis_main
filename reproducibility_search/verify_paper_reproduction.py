"""Validate complete paper-first artifacts and an optional exact frozen rerun."""
import argparse
import itertools
import json
from dataclasses import asdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit,unquote

import numpy as np
import pandas as pd
from PIL import Image

import paper_reproduction as pr


class Links(HTMLParser):
    def __init__(self):
        super().__init__();self.links=[]

    def handle_starttag(self,tag,attrs):
        self.links.extend(v for k,v in attrs if k in ("href","src") and v)


def read_csv(path):
    return pd.read_csv(path,float_precision="round_trip")


def check_image(path,size=None):
    with Image.open(path) as image:
        if size:assert image.size==size,(str(path),image.size)
        image.verify()


def validate(run,frozen=None):
    out=Path(run).resolve()
    manifest=json.loads((out/"manifest.json").read_text())
    spec=pr.load_config(out/"paper_reproduction_config.json")
    inputs=pr.load_inputs(pr.ROOT/"InputData.xlsx")
    original_hashes=pr.original_hashes()
    assert original_hashes==manifest["original_hashes"],"Protected files changed"
    # result is tied to the exact model code
    assert pr.digest(pr.__file__)==manifest["implementation_sha256"],"Implementation changed"
    assert inputs.fingerprint()==manifest["input_values_fingerprint"],"Input arrays changed"
    # Known protected originals, independent of this run's own manifest.
    expected={
        "NRHES_Model.py":"4434541cf463a82b0bb3cf4d3dc6b7315970d08e03205a232fc3c0981fb151a9",
        "NRHES_Model.m":"945462d21e855beaa4427f1fe8a5d4bc78b378ab21305911401416f765227cf9",
        "InputData.xlsx":"54c1d34a147db99d2f7a1e8137527571ba35834ae5c2862cc0a48949b3d2f8e3",
    }
    assert all(original_hashes[k]==v for k,v in expected.items()),"Known original checksum differs"
    rank=read_csv(out/"ranking.csv")
    all_rows=read_csv(out/"all_results.csv")
    cases=spec["benchmarks_2025"]
    ids=[c["id"] for c in cases]
    count=len(rank)
    physical_count=len(list(itertools.product(*spec["physical_grid"].values())))
    finance_count=len(list(itertools.product(*spec["economic_grid"].values())))
    shortlist=json.loads((out/"financial_shortlist.json").read_text())
    default_in_grid=all(v in spec["economic_grid"][k] for k,v in asdict(pr.Finance()).items())
    assert count==physical_count+len(shortlist)*(finance_count-int(default_in_grid))+1
    assert count==manifest["grid_cases"]
    assert len(all_rows)==count*len(cases)==manifest["all_2025_metric_rows"]
    assert rank.case_id.is_unique
    assert set(p.name for p in (out/"cases").iterdir())==set(rank.case_id)
    settings_seen=set();plots_seen=set();groups=dict(tuple(all_rows.groupby("case_id",sort=False)))
    for r in rank.to_dict("records"):
        folder=out/"cases"/r["case_id"]
        settings=json.loads((folder/"settings.json").read_text())
        signature=json.dumps(settings,sort_keys=True)
        assert signature not in settings_seen,"Duplicate complete settings"
        settings_seen.add(signature)
        check_image(folder/"candidate.png",(2500,1630))
        fingerprint=pr.digest(folder/"candidate.png")
        assert fingerprint not in plots_seen,"A dashboard was reused"
        plots_seen.add(fingerprint)
        rows=read_csv(folder/"results.csv")
        assert rows.id.tolist()==ids
        pd.testing.assert_frame_equal(rows,groups[r["case_id"]].drop(columns="case_id").reset_index(drop=True),check_dtype=False)
        summary=pr.errors(rows.to_dict("records"),cases)
        for key,value in summary.items():
            assert np.isclose(value,r[key],rtol=0,atol=1e-9),(r["case_id"],key)
        assert rows.valid.all()==r["valid"]
        for k,v in {**settings["physics"],**settings["finance"]}.items():
            if isinstance(v,(float,int)):
                assert np.isclose(v,r[k],rtol=0,atol=1e-12),(r["case_id"],k)
            else:
                assert v==r[k],(r["case_id"],k)
    physical_folders=list((out/"physical").iterdir())
    assert len(physical_folders)==physical_count==manifest["physical_dispatches"]
    max_physical_residual=0
    for folder in physical_folders:
        audits=json.loads((folder/"audits.json").read_text())
        assert [a["id"] for a in audits]==ids
        records=rank[(rank.physical_id==folder.name)&(rank.stage=="physical_grid")]
        assert len(records)==1
        assert all(a["valid"] for a in audits)==bool(records.valid.iloc[0])
        max_physical_residual=max(max_physical_residual,max(a["max_balance_residual_MWh"] for a in audits))
        check_image(folder/"curves.png",(2500,1630))
    diagnostic=rank[rank.stage=="invalid_additive_diagnostic"]
    assert len(diagnostic)==1 and not diagnostic.valid.iloc[0]
    assert diagnostic.case_id.iloc[0]!=manifest["selected_case"]
    assert any(a["max_H2_resource_violation"]>0 for a in json.loads((out/"additive_diagnostic_audits.json").read_text()))
    links_checked=0
    for page in [out/"index.html",*sorted((out/"cases").glob("*/index.html"))]:
        parser=Links();parser.feed(page.read_text())
        for value in parser.links:
            url=urlsplit(value)
            if not url.scheme and url.path:
                assert (page.parent/unquote(url.path)).resolve().exists(),(str(page),value)
                links_checked+=1
    npz_count=0;max_bundle_residual=0
    folders=[(out/"selected",cases),(out/"paper_first_reference",cases)]
    folders.extend((folder,spec["benchmarks_2024"]) for folder in sorted((out/"paper_2024").iterdir()))
    if frozen:folders.append((Path(frozen).resolve(),cases))
    for folder,benchmarks in folders:
        settings=json.loads((folder/"settings.json").read_text())
        p=pr.Physics(**settings["physics"]);f=pr.Finance(**settings["finance"])
        rows=read_csv(folder/"results.csv")
        assert len(list(folder.glob("row_*.npz")))==len(benchmarks)
        assert rows.id.tolist()==[c["id"] for c in benchmarks]
        saved_audits=json.loads((folder/"audits.json").read_text())
        for c,row,saved_audit in zip(benchmarks,rows.to_dict("records"),saved_audits):
            with np.load(folder/f"row_{c['id']}.npz") as arrays:
                assert set(arrays.files)==set(pr.FIELDS)
                s={k:arrays[k] for k in arrays.files}
            assert all(v.shape==(8760,) and np.isfinite(v).all() for v in s.values())
            np.testing.assert_array_equal(s["Load"],inputs.load)
            np.testing.assert_array_equal(s["Hour"],inputs.hours)
            a=pr.audit(s,c);assert a["valid"] and saved_audit["valid"]
            max_bundle_residual=max(max_bundle_residual,a["max_balance_residual_MWh"])
            thermal=50 if c["paper"]==2024 else p.reactor_2025_mwth
            m=pr.summarize(s,thermal);m.update(pr.economics(m,c,p,f))
            for key in ("DPSP","EWP","LCOE","H2_kg","tank_kg"):
                assert np.isclose(m[key],row[key],rtol=1e-12,atol=1e-12),(str(folder),c["id"],key)
            check_image(folder/f"row_{c['id']}.png")
            npz_count+=1
        check_image(folder/"candidate.png",(2500,1630))
    teacher=read_csv(out/"teacher/results.csv")
    assert teacher.id.tolist()==ids
    for c,row in zip(cases,teacher.to_dict("records")):
        with np.load(out/"teacher"/f"row_{c['id']}.npz") as arrays:
            assert all(arrays[k].shape==(8760,) and np.isfinite(arrays[k]).all() for k in arrays.files)
            np.testing.assert_array_equal(arrays["Load"],inputs.load)
            assert float(np.abs(arrays["total_residual"]).max())==row["max_balance_residual_MWh"]
        check_image(out/"teacher"/f"row_{c['id']}.png");npz_count+=1
    frozen_exact=None
    if frozen:
        frozen=Path(frozen).resolve()
        assert (frozen/"results.csv").read_bytes()==(out/"selected/results.csv").read_bytes()
        assert (frozen/"settings.json").read_bytes()==(out/"selected/settings.json").read_bytes()
        for c in cases:
            with np.load(frozen/f"row_{c['id']}.npz") as a,np.load(out/"selected"/f"row_{c['id']}.npz") as b:
                for key in a.files:np.testing.assert_array_equal(a[key],b[key])
        frozen_exact=True
    battery=read_csv(out/"paper_2024_all_controls.csv")
    assert len(battery)==40 and (battery.DPSP==0).all()
    algorithm=read_csv(out/"paper_2024_Table5_algorithm_reference.csv")
    assert len(algorithm)==4 and algorithm.reproduction_status.str.startswith("reference_only").all()
    check_image(out/"teacher_vs_paper_first.png",(2100,1150))
    assert original_hashes==pr.original_hashes() and inputs.fingerprint()==manifest["input_values_fingerprint"]
    result={
        "status":"passed","complete_configurations":count,"unique_settings":len(settings_seen),
        "unique_candidate_plots":len(plots_seen),"all_2025_result_rows":len(all_rows),
        "physical_dispatches":physical_count,"physical_audit_rows":physical_count*10,
        "HTML_links_checked":links_checked,"hourly_NPZs_checked":npz_count,
        "hours_per_NPZ":8760,"frozen_exact":frozen_exact,"original_files_unchanged":True,
        "exact_Excel_values_unchanged":True,"max_physical_balance_residual_MWh":max_physical_residual,
        "max_bundle_balance_residual_MWh":max_bundle_residual,
        "2024_control_rows_with_zero_DPSP":len(battery),
        "teacher_rows_with_aggregate_balance_failures":int((teacher.max_balance_residual_MWh>1e-7).sum()),
    }
    pr.save_json(out/"artifact_validation.json",result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run",type=Path,required=True)
    parser.add_argument("--frozen",type=Path)
    args=parser.parse_args()
    print(json.dumps(validate(args.run,args.frozen),indent=2))


if __name__=="__main__":
    main()

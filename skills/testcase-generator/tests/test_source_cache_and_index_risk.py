import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1]/"scripts"
sys.path.insert(0,str(SCRIPTS))
import source_documents as sources
import codegraph_impact_provider as graph
import business_impact_gate as gate
import workflow_analysis_legacy as analysis

class SourceCacheTests(unittest.TestCase):
    def setup_import(self,root,entries=None):
        entries=entries or [
            {"kind":"requirement","title":"需求","url":"https://example.com/req","page_version":1,"markdown":"# 需求\n\n## 需求背景\n账号隔离\n\n## 解决方案\n使用业务账号\n","raw_payload":{"version":1}},
            {"kind":"test","title":"PRJ-1 提测","url":"https://example.com/test","page_version":1,"markdown":"# 提测\n涉及服务：tax-data\n"},
            {"kind":"design","title":"方案","url":"https://example.com/design","page_version":1,"markdown":"# 设计\n按库授权\n"},
        ]
        p=root/"incoming.json";p.write_text(json.dumps({"documents":entries},ensure_ascii=False),encoding="utf-8")
        return p
    def test_saves_standard_folders_raw_response_and_metadata(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);p=self.setup_import(root);result=sources.prepare_source_documents(root,str(p))
            records=result["documents"]
            self.assertEqual(0,result["network_requests"])
            self.assertEqual({"需求文档","提测文档"},{Path(d["local_path"]).parent.name for d in records})
            self.assertTrue(Path(records[0]["original_payload"]).exists())
            self.assertEqual(1,records[0]["page_version"])
            self.assertTrue(all(d.get("markdown_sha256") and d.get("fetched_at") for d in records))
    def test_reuses_cache_when_incoming_temporary_body_is_gone(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);p=self.setup_import(root);first=sources.prepare_source_documents(root,str(p))
            incoming=json.loads(p.read_text(encoding="utf-8"))
            for d in incoming["documents"]:d.pop("markdown");d["markdown_path"]=str(root/"deleted.md")
            p.write_text(json.dumps(incoming),encoding="utf-8")
            second=sources.prepare_source_documents(root,str(p))
            self.assertEqual([d["current_sha256"] for d in first["documents"]],[d["current_sha256"] for d in second["documents"]])
            p.unlink()
            self.assertEqual("local_reused",sources.prepare_source_documents(root)["status"])
    def test_manual_edit_is_kept_and_disclosed(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);result=sources.prepare_source_documents(root,str(self.setup_import(root)))
            p=Path(result["documents"][0]["local_path"]);p.write_text("# 用户修订\n",encoding="utf-8")
            reused=sources.prepare_source_documents(root)
            self.assertEqual("local_modified",reused["documents"][0]["cache_status"])
            self.assertEqual("# 用户修订\n",p.read_text(encoding="utf-8"))
    def test_refresh_changes_version_and_preserves_previous_body(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);p=self.setup_import(root);sources.prepare_source_documents(root,str(p))
            incoming=json.loads(p.read_text(encoding="utf-8"))
            incoming["documents"][0].update(page_version=2,markdown="# 新版本\n")
            p.write_text(json.dumps(incoming),encoding="utf-8")
            result=sources.prepare_source_documents(root,str(p),refresh=True)
            self.assertEqual(2,result["documents"][0]["page_version"])
            history=list((root/".ai-internal/source_history").rglob("需求.md"))
            self.assertEqual(1,len(history));self.assertIn("账号隔离",history[0].read_text(encoding="utf-8"))
            old_manifest=next((root/".ai-internal/source_history").rglob("source_documents_manifest.json"))
            self.assertEqual(1,json.loads(old_manifest.read_text(encoding="utf-8"))["documents"][0]["page_version"])
    def test_invalid_batch_does_not_save_partial_authoritative_files(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);p=self.setup_import(root,[{"kind":"requirement","title":"有效","url":"https://example.com/a","markdown":"有效正文"},{"kind":"test","url":"https://example.com/b","markdown":""}])
            with self.assertRaises(ValueError):sources.prepare_source_documents(root,str(p))
            self.assertFalse((root/"需求文档").exists())
            self.assertFalse((root/sources.MANIFEST).exists())
    def test_escape_filename_and_missing_cache_fail_without_fetch(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);p=self.setup_import(root,[{"kind":"requirement","url":"https://example.com/a","filename":"../outside.md","markdown":"正文"}])
            with self.assertRaises(ValueError):sources.prepare_source_documents(root,str(p))
            p=self.setup_import(root);result=sources.prepare_source_documents(root,str(p));Path(result["documents"][0]["local_path"]).unlink()
            with self.assertRaises(ValueError):sources.prepare_source_documents(root)
    def test_manifest_limits_analysis_to_current_sources(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);result=sources.prepare_source_documents(root,str(self.setup_import(root)))
            (root/"旧资料.md").write_text("不得进入事实",encoding="utf-8")
            actual=set(analysis.iter_source_markdown_files(root))
            self.assertEqual({Path(d["local_path"]) for d in result["documents"]},actual)
    def test_saved_design_url_is_available_in_review_inventory(self):
        from source_inventory import build_source_inventory
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);sources.prepare_source_documents(root,str(self.setup_import(root)))
            inv=build_source_inventory("[设计方案](https://example.com/design)",root,root/"normalized.md")
            self.assertEqual("linked_available",inv["attachments"][0]["availability"])
            self.assertFalse(inv["attachments"][0]["decision_required"])
    def test_main_imports_before_resolving_empty_requirement_package(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)/"需求包";root.mkdir();p=self.setup_import(root)
            p.rename(root/".incoming.json");p=root/".incoming.json"
            r=subprocess.run([sys.executable,str(SCRIPTS/"main.py"),str(root),"--step","check","--source-documents",str(p)],capture_output=True,encoding="utf-8",timeout=30)
            self.assertEqual(0,r.returncode,r.stdout+r.stderr)
            self.assertTrue((root/"需求文档/需求.md").exists())

class IndexRiskTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.repo=Path(self.temp.name)
        self.git("init","-q");self.git("config","user.email","test@example.invalid");self.git("config","user.name","test")
        (self.repo/"Job.java").write_text("class Job {\n void run() { service.apply(); }\n}\n",encoding="utf-8")
        (self.repo/"Service.java").write_text("class Service {\n void apply() { save(); }\n}\n",encoding="utf-8")
        self.git("add",".");self.git("commit","-qm","fixture");self.sha=self.git("rev-parse","HEAD").strip()
        self.plan={"targets":[{"target_id":"t","repo_path":str(self.repo),"target_sha":self.sha,"indexed_sha":"b"*40,"index_aligned":False,"max_files":30,"required_source_files":["Job.java","Service.java"],"codegraph_policy":"risk_based"}]}
        self.proof={"target_sha":self.sha,"coverage_status":"complete","unresolved_critical":[],"critical_paths":[{"path_id":"main","evidence_ids":["E1","E2","E3"]}],"evidence":[
            {"evidence_id":"E1","kind":"entry","file":"Job.java","start_line":1,"end_line":3,"source":"class Job {\n void run() { service.apply(); }\n}"},
            {"evidence_id":"E2","kind":"rule","file":"Service.java","start_line":1,"end_line":3,"source":"class Service {\n void apply() { save(); }\n}"},
            {"evidence_id":"E3","kind":"call","file":"Job.java","start_line":2,"source":" void run() { service.apply(); }","caller":"Job.run","callee":"Service.apply","hop":1},
        ]}
    def tearDown(self):self.temp.cleanup()
    def git(self,*args):
        r=subprocess.run(["git","-C",str(self.repo),*args],capture_output=True,text=True,encoding="utf-8",check=True)
        return r.stdout
    def result(self,proof=None,plan=None,**extra):
        payload={"targets":[{"target_id":"t",**extra}]}
        if proof is not None:payload["targets"][0]["source_verification"]=proof
        return graph.validate_codegraph_results(plan or self.plan,payload)["targets"][0]
    def test_stale_graph_is_navigation_only(self):
        r=self.result(relations=[{"caller":"Old","callee":"Gone","hop":1}],files=["Old.java"])
        self.assertEqual("stale_index",r["status"]);self.assertEqual([],r["relations"])
        self.assertEqual(1,len(r["navigation_relations"]))
    def test_source_proof_allows_stale_index_without_claiming_graph_ready(self):
        r=self.result(self.proof)
        self.assertEqual("source_verified",r["status"]);self.assertEqual("source",r["accepted_evidence"])
        self.assertEqual("stale_index",r["graph_status"])
        self.assertEqual(["Job.java","Service.java"],r["files"])
    def test_missing_index_can_use_fresh_source_proof(self):
        plan=copy.deepcopy(self.plan);plan["targets"][0]["indexed_sha"]=""
        self.assertEqual("source_verified",self.result(self.proof,plan)["status"])
    def test_explicit_strict_mode_still_requires_aligned_graph(self):
        plan=copy.deepcopy(self.plan);plan["targets"][0]["codegraph_policy"]="strict"
        self.assertEqual("blocked_index_mismatch",self.result(self.proof,plan)["status"])
    def test_wrong_sha_forged_source_and_missing_file_cannot_pass(self):
        for mutation in ["sha","source","file","gap"]:
            with self.subTest(mutation=mutation):
                proof=copy.deepcopy(self.proof)
                if mutation=="sha":proof["target_sha"]="c"*40
                if mutation=="source":proof["evidence"][1]["source"]="fabricated"
                if mutation=="file":proof["critical_paths"][0]["evidence_ids"]=["E1"]
                if mutation=="gap":proof["unresolved_critical"]=["dynamic dispatch"]
                self.assertNotEqual("source_verified",self.result(proof)["status"])
    def test_source_proof_works_with_aligned_index_without_mcp_query(self):
        plan=copy.deepcopy(self.plan);plan["targets"][0].update(indexed_sha=self.sha,index_aligned=True)
        self.assertEqual("source_verified",self.result(self.proof,plan)["status"])
    def test_cross_file_path_requires_a_verified_call(self):
        proof=copy.deepcopy(self.proof);proof["critical_paths"][0]["evidence_ids"]=["E1","E2"]
        self.assertNotEqual("source_verified",self.result(proof)["status"])
    def test_invalid_file_payload_fails_as_partial_coverage(self):
        proof=copy.deepcopy(self.proof);proof["evidence"][0]["file"]=["not","a","path"]
        self.assertNotEqual("source_verified",self.result(proof)["status"])
    def test_source_commit_remains_frozen_when_branch_moves(self):
        (self.repo/"Service.java").write_text("class Service { void apply() { other(); } }\n",encoding="utf-8")
        self.git("add",".");self.git("commit","-qm","later change")
        self.assertNotEqual(self.sha,self.git("rev-parse","HEAD").strip())
        self.assertEqual("source_verified",self.result(self.proof)["status"])
    def test_excessive_hops_are_rejected(self):
        self.assertEqual("blocked_result_bounds",self.result(relations=[{"caller":"A","callee":"B","hop":3}])["status"])
    def test_source_verification_is_reported_without_false_pending_gate_label(self):
        import business_impact_translator
        target={"target_id":"t","analysis_mode":"uat","effective_analysis_mode":"uat","codegraph_status":"source_verified","uat_merge_gate":{"status":"fully_merged"},"analysis":{"files_changed":["Job.java"],"entry_points":["Job.run"],"business_rules":["保存业务数据"]}}
        graph_data={"targets":[{"target_id":"t","status":"source_verified","relations":[],"files":["Job.java"]}]}
        result=business_impact_translator.translate_business_impacts({"items":[]},[target],graph_data,{"project_name":"示例"})
        self.assertEqual("uat_source_verified",result["analysis_mode"])
    def test_gate_checks_source_coverage_and_uat_freshness(self):
        impact={"items":[{"impact_id":"BI","business_domain":"业务","affected_actor":["用户"],"impact_category":"任务","validation_object":"Job","business_scenario":"执行","trigger":"job","preconditions":["就绪"],"new_behavior":"落库","expected_result":["落库"],"test_focus":["结果核对"],"confidence":"high","evidence_ids":["CE-1"]}]}
        target={"target_id":"t","status":"completed","analysis_mode":"uat","codegraph_policy":"risk_based","codegraph_status":"source_verified","uat_merge_gate":{"status":"fully_merged"},"git_snapshot":{"formal_ready":True},"analysis":{"files_changed":["Job.java"],"technology_coverage":{"complete":True,"gaps":[]}}}
        self.assertEqual("passed",gate.assess_business_impact_gate(impact,[target])["summary"]["status"])
        target["codegraph_status"]="stale_index"
        result=gate.assess_business_impact_gate(impact,[target])
        self.assertFalse(next(g for g in result["gates"] if g["gate_id"]=="BI-G4")["blocking"])
        self.assertTrue(next(g for g in result["gates"] if g["gate_id"]=="BI-G4-S")["blocking"])
        target["codegraph_status"]="source_verified";target["git_snapshot"]["formal_ready"]=False
        self.assertTrue(next(g for g in gate.assess_business_impact_gate(impact,[target])["gates"] if g["gate_id"]=="BI-G3-F")["blocking"])

if __name__=="__main__":unittest.main()

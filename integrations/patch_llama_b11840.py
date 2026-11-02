"""Apply metadata-only instrumentation to upstream llama.cpp b11840."""
import hashlib,pathlib,sys
BASE_SHA256 = 'dff696ee775fcf0cba56a2c7d99e73c3a7d955011730b43896797b16d8aa824c'
TASK_SHA256 = '557cbcc3d31d240fbbef41bb0ea9f139e0a8bc35c23282e720db59ab74711905'
BLOCK = '\n            // Metadata only: bridge native task timing to the returned completion ID.\n            auto safe_id = [](const std::string & value) {\n                return !value.empty() && value.size() <= 200 &&\n                    value.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-") == std::string::npos;\n            };\n            auto header = [&req](const std::string & name) {\n                for (const auto & item : req.headers) {\n                    auto key = item.first;\n                    for (auto & ch : key) ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));\n                    if (key == name) return item.second;\n                }\n                return std::string();\n            };\n            json identity = {{"task", task.id}, {"completion_id", completion_id}};\n            auto source = header("x-lmc-origin");\n            auto sid = header("x-lmc-session-id");\n            auto tid = header("x-lmc-task-id");\n            auto host = header("x-lmc-host");\n            if (source.empty() && header("user-agent").find("deepseek-harness/") != std::string::npos) {\n                const auto original = json::parse(req.body);\n                if (original.is_object() && original.contains("prompt_cache_key") && original["prompt_cache_key"].is_string()) {\n                    sid = original["prompt_cache_key"].get<std::string>();\n                }\n                if (sid.empty()) sid = header("session_id");\n                source = "DSH"; tid = sid;\n            }\n            if ((source == "Hermes" || source == "DSH") && safe_id(sid) && safe_id(tid)) {\n                identity["origin"] = source; identity["session_id"] = sid; identity["task_id"] = tid;\n                if (safe_id(host)) identity["host"] = host;\n            }\n            task.params.lmc_request_identity = identity;\n            SRV_INF("LMC_REQUEST %s\\n", identity.dump().c_str());\n'

def apply(root):
    root=pathlib.Path(root);p=root/'tools/server/server-context.cpp';q=root/'tools/server/server-task.h'
    source=p.read_text();header=q.read_text()
    if hashlib.sha256(source.encode()).hexdigest()!=BASE_SHA256 or hashlib.sha256(header.encode()).hexdigest()!=TASK_SHA256:
        raise SystemExit('Source differs from pinned b11840; refusing patch')
    needle='            task.params.oaicompat_model   = meta->model_name;'
    source=source.replace(needle,needle+BLOCK,1)
    slot='        const auto & ptask = task ? task : task_prev;'
    live='        // Never expose a previous request as current activity.\n        if (task && is_processing() && task->params.lmc_request_identity.is_object()) {\n            res["request_identity"] = task->params.lmc_request_identity;\n        }\n\n'
    assert source.count(slot)==1
    source=source.replace(slot,live+slot,1)
    field='    std::string        oaicompat_cmpl_id;'
    assert field in header
    header=header.replace(field,field+'\n    json               lmc_request_identity; // IDs only; never prompt, reply or credentials',1)
    p.write_text(source);q.write_text(header)

if __name__=='__main__':apply(sys.argv[1])

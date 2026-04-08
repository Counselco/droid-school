#!/usr/bin/env python3
"""
DroidSchool Memory Analyzer v1.0
Scans all known memory files, analyzes with Claude Sonnet,
produces a comprehensive report of what the droid knows.

Usage:
  python3 memory_analyzer.py --droid ~max --workspace /Users/joseph/.openclaw/workspace
  python3 memory_analyzer.py --droid ~claudie --workspace /home/josep/.hermes/supervisor

Requires: ANTHROPIC_API_KEY in environment
"""

import os, sys, json, requests, argparse
from pathlib import Path
from datetime import datetime

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DAG = "https://dag.tibotics.com"

# Known memory file locations per droid type
OPENCLAW_FILES = [
    "DROID.md", "TASKS.md", "JOSEPH.md", "MICHELLE.md",
    "DROIDS.md", "CHRONX.md", "DROIDSCHOOL.md", "ECOMMERCE.md",
    "WALMART.md", "MEMORY.md", "USER.md", "SOUL.md", "AGENTS.md",
    "IDENTITY.md", "identity.sealed"
]

HERMES_FILES = [
    "CLAUDE.md", "MEMORY.md", "IDENTITY.md",
    "DROID.md", "TASKS.md", "JOSEPH.md", "DROIDS.md"
]

SKILLS_EXTENSIONS = [".md", ".txt", ".json"]

def scan_workspace(workspace_path):
    """Scan workspace directory for all readable files."""
    wp = Path(workspace_path)
    found = {}
    missing = []

    print(f"\nScanning: {workspace_path}")

    # Check known files
    known = OPENCLAW_FILES if "openclaw" in str(workspace_path).lower() else HERMES_FILES
    for fname in known:
        fpath = wp / fname
        if fpath.exists():
            try:
                content = fpath.read_text(errors='replace')
                found[fname] = {
                    "path": str(fpath),
                    "size": len(content),
                    "modified": datetime.fromtimestamp(fpath.stat().st_mtime).isoformat(),
                    "content": content[:8000]  # Cap at 8KB per file
                }
                print(f"  ✓ {fname} ({len(content)} chars)")
            except Exception as e:
                print(f"  ✗ {fname} (error: {e})")
        else:
            missing.append(fname)
            print(f"  — {fname} (not found)")

    # Scan skills directory
    skills_dir = wp / "skills"
    if skills_dir.exists():
        skill_files = list(skills_dir.glob("**/*"))
        skill_files = [f for f in skill_files if f.is_file()
                      and f.suffix in SKILLS_EXTENSIONS]
        print(f"  📚 Skills directory: {len(skill_files)} files")
        found["_skills_count"] = len(skill_files)
        found["_skills_list"] = [str(f.relative_to(wp)) for f in skill_files[:20]]

    # Check for identity.sealed
    sealed = wp / "identity.sealed"
    if sealed.exists():
        try:
            data = json.loads(sealed.read_text())
            found["_identity_sealed"] = {
                "serial": data.get("serial", "unknown"),
                "droid_name": data.get("droid_name", "unknown"),
                "operator": data.get("operator", "unknown"),
                "enrolled_at": data.get("enrolled_at", "unknown"),
            }
            print(f"  🔐 identity.sealed: serial={data.get('serial','?')}")
        except:
            pass

    return found, missing

def fetch_dag_record(droid_name):
    """Fetch droid's certification record from DroidSchool DAG."""
    name = droid_name.lstrip('~')
    print(f"\nFetching DAG record for {droid_name}...")

    try:
        # Check roster
        r = requests.get(f"{DAG}/roster", timeout=10)
        roster = r.json()
        droid_record = next((d for d in roster
                           if d.get('name','').lstrip('~') == name
                           or d.get('userid','') == name), None)

        # Check exam results
        skills = requests.get(f"{DAG}/list", timeout=10).json()
        exams = [s for s in skills
                if s.get('tier') == 'exam_result'
                and name in s.get('skill_name', '').lower()]

        bootcamp = [s for s in skills
                   if 'BOOTCAMP' in s.get('skill_name', '')
                   and name in s.get('skill_name', '').lower()]

        return {
            "roster_entry": droid_record,
            "exam_count": len(exams),
            "bootcamp_results": bootcamp,
            "total_skills": len([s for s in skills
                                if s.get('tier') not in
                                ['exam_result','system_event','violation']]),
        }
    except Exception as e:
        print(f"  DAG unreachable: {e}")
        return {}

def analyze_with_ai(droid_name, files_found, files_missing, dag_record, api_key, provider=None):
    """Use selected AI provider to analyze all memory and produce a report."""

    if provider is None:
        provider = {"name": "Anthropic Claude",
                   "url": ANTHROPIC_URL,
                   "model": "claude-sonnet-4-20250514",
                   "type": "anthropic"}

    print(f"\nAnalyzing with {provider['name']} ({provider['model']})...")

    # Build context
    context_parts = []
    for fname, fdata in files_found.items():
        if fname.startswith('_'):
            continue
        context_parts.append(f"=== {fname} (modified: {fdata['modified']}) ===\n{fdata['content']}\n")
    context = "\n".join(context_parts)

    skills_count = files_found.get('_skills_count', 0)
    skills_list = files_found.get('_skills_list', [])
    identity = files_found.get('_identity_sealed', {})

    system_prompt = """You are the DroidSchool Memory Analyzer.
You have been given all the memory files from an enrolled AI droid.
Your job is to produce a clear, structured analysis of what this droid knows,
what is missing, what conflicts exist, and what the operator needs to know.
Be specific and actionable. Use plain English. Format with markdown headers.
Highlight problems with WARNING:. This report is read by the operator."""

    user_message = f"""Analyze the memory state of droid {droid_name}.

FILES FOUND: {', '.join(f for f in files_found if not f.startswith('_'))}
FILES MISSING: {', '.join(files_missing) if files_missing else 'none'}
SKILLS INSTALLED: {skills_count} files
DAG RECORD: {json.dumps(dag_record, indent=2)}
IDENTITY: {json.dumps(identity, indent=2)}

MEMORY CONTENT:
{context[:12000]}

Report on:
1. WHAT THIS DROID KNOWS — by category (operator, projects, network, tasks)
2. MEMORY GAPS — missing or unpersisted information
3. CONFLICTS — contradictions between files
4. LINKED FILES — which references are broken
5. STALENESS — files not updated recently
6. CERTIFICATION STATUS — DroidSchool enrollment and courses
7. TOP 3 RECOMMENDATIONS — what operator should do right now

Under 800 words. Be direct. Flag urgent issues clearly."""

    try:
        ptype = provider.get("type", "openai_compat")

        # ── ANTHROPIC ──────────────────────────────────────────
        if ptype == "anthropic":
            resp = requests.post(provider["url"],
                headers={"Content-Type": "application/json",
                         "x-api-key": api_key,
                         "anthropic-version": "2023-06-01"},
                json={"model": provider["model"], "max_tokens": 1500,
                      "system": system_prompt,
                      "messages": [{"role": "user", "content": user_message}]},
                timeout=60)
            data = resp.json()
            if data.get('error'):
                return f"Error: {data['error']['message']}"
            return data['content'][0]['text']

        # ── OPENAI / COMPATIBLE (Mistral, Together, Groq, xAI, LM Studio, Ollama, Custom) ──
        elif ptype in ("openai_compat", "local"):
            headers = {"Content-Type": "application/json"}
            if ptype != "local" and api_key != "local":
                headers["Authorization"] = f"Bearer {api_key}"
            resp = requests.post(provider["url"],
                headers=headers,
                json={"model": provider["model"], "max_tokens": 1500,
                      "messages": [
                          {"role": "system", "content": system_prompt},
                          {"role": "user", "content": user_message}
                      ]},
                timeout=120)
            data = resp.json()
            if data.get('error'):
                return f"Error: {data['error']['message']}"
            return data['choices'][0]['message']['content']

        # ── GOOGLE GEMINI ──────────────────────────────────────
        elif ptype == "gemini":
            url = f"{provider['url']}?key={api_key}"
            resp = requests.post(url,
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [
                    {"text": system_prompt + "\n\n" + user_message}
                ]}]},
                timeout=60)
            data = resp.json()
            if data.get('error'):
                return f"Error: {data['error']['message']}"
            return data['candidates'][0]['content']['parts'][0]['text']

        # ── COHERE ─────────────────────────────────────────────
        elif ptype == "cohere":
            resp = requests.post(provider["url"],
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {api_key}"},
                json={"model": provider["model"],
                      "messages": [
                          {"role": "system", "content": system_prompt},
                          {"role": "user", "content": user_message}
                      ]},
                timeout=60)
            data = resp.json()
            if data.get('message'):
                return f"Error: {data['message']}"
            return data['message']['content'][0]['text']

        else:
            return f"Unknown provider type: {ptype}"

    except Exception as e:
        return f"Analysis error: {e}"

def save_report(droid_name, report, workspace):
    """Save the analysis report to the workspace."""
    wp = Path(workspace)
    report_path = wp / f"MEMORY_ANALYSIS_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
    report_path.write_text(f"# Memory Analysis — {droid_name}\nGenerated: {datetime.now().isoformat()}\n\n{report}")
    print(f"\nReport saved: {report_path}")
    return str(report_path)

def main():
    parser = argparse.ArgumentParser(description="DroidSchool Memory Analyzer")
    parser.add_argument("--droid", required=True, help="Droid name e.g. ~max")
    parser.add_argument("--workspace", required=True, help="Path to droid's workspace directory")
    parser.add_argument("--no-ai", action="store_true", help="Skip AI analysis, just scan files")
    parser.add_argument("--anthropic-key", help="Anthropic API key (or ANTHROPIC_API_KEY env var)")
    args = parser.parse_args()

    api_key = args.anthropic_key or os.environ.get("ANTHROPIC_API_KEY")

    print(f"\n{'='*60}")
    print(f"  DROIDSCHOOL MEMORY ANALYZER v1.0")
    print(f"  Droid: {args.droid}")
    print(f"  Workspace: {args.workspace}")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")

    # Scan files
    files_found, files_missing = scan_workspace(args.workspace)

    # Fetch DAG record
    dag_record = fetch_dag_record(args.droid)

    # If no API key provided, show provider selection menu
    if not api_key and not args.no_ai:
        print("\n" + "="*60)
        print("  ANALYSIS MODE — SELECT YOUR AI PROVIDER")
        print("="*60)
        print()
        print("  Cloud providers (requires API key):")
        print("  [1]  Anthropic Claude    console.anthropic.com")
        print("  [2]  OpenAI GPT          platform.openai.com")
        print("  [3]  Google Gemini       aistudio.google.com")
        print("  [4]  Mistral             console.mistral.ai")
        print("  [5]  Cohere              dashboard.cohere.com")
        print("  [6]  Together AI         api.together.xyz")
        print("  [7]  Groq                console.groq.com")
        print("  [8]  xAI (Grok)          console.x.ai")
        print()
        print("  Local providers (no API key needed):")
        print("  [9]  LM Studio           localhost:1234")
        print("  [10] Ollama              localhost:11434")
        print("  [11] Custom endpoint     enter your own URL")
        print()
        print("  [Enter] Skip AI — full memory dump (nothing sent anywhere)")
        print()

        choice = input("  Choose [1-11 or Enter]: ").strip()

        PROVIDERS = {
            "1":  {"name": "Anthropic Claude",
                   "url": ANTHROPIC_URL,
                   "model": "claude-sonnet-4-20250514",
                   "type": "anthropic",
                   "key_hint": "sk-ant-..."},
            "2":  {"name": "OpenAI GPT",
                   "url": "https://api.openai.com/v1/chat/completions",
                   "model": "gpt-4o",
                   "type": "openai",
                   "key_hint": "sk-..."},
            "3":  {"name": "Google Gemini",
                   "url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent",
                   "model": "gemini-1.5-pro",
                   "type": "gemini",
                   "key_hint": "AIza..."},
            "4":  {"name": "Mistral",
                   "url": "https://api.mistral.ai/v1/chat/completions",
                   "model": "mistral-large-latest",
                   "type": "openai_compat",
                   "key_hint": "..."},
            "5":  {"name": "Cohere",
                   "url": "https://api.cohere.ai/v2/chat",
                   "model": "command-r-plus",
                   "type": "cohere",
                   "key_hint": "..."},
            "6":  {"name": "Together AI",
                   "url": "https://api.together.xyz/v1/chat/completions",
                   "model": "meta-llama/Llama-3-70b-chat-hf",
                   "type": "openai_compat",
                   "key_hint": "..."},
            "7":  {"name": "Groq",
                   "url": "https://api.groq.com/openai/v1/chat/completions",
                   "model": "llama3-70b-8192",
                   "type": "openai_compat",
                   "key_hint": "gsk_..."},
            "8":  {"name": "xAI Grok",
                   "url": "https://api.x.ai/v1/chat/completions",
                   "model": "grok-beta",
                   "type": "openai_compat",
                   "key_hint": "xai-..."},
            "9":  {"name": "LM Studio (local)",
                   "url": "http://localhost:1234/v1/chat/completions",
                   "model": "local-model",
                   "type": "local",
                   "key_hint": None},
            "10": {"name": "Ollama (local)",
                   "url": "http://localhost:11434/v1/chat/completions",
                   "model": "llama3",
                   "type": "local",
                   "key_hint": None},
            "11": {"name": "Custom endpoint",
                   "url": None,
                   "model": None,
                   "type": "openai_compat",
                   "key_hint": None},
        }

        if not choice or choice not in PROVIDERS:
            args.no_ai = True
            print("\n  Running full local dump — no data leaves your machine.")
        else:
            provider = PROVIDERS[choice]
            print(f"\n  Selected: {provider['name']}")

            if choice == "11":
                provider["url"] = input("  Endpoint URL: ").strip()
                provider["model"] = input("  Model name: ").strip()

            if provider["type"] == "local":
                api_key = "local"
                print(f"  Using local endpoint: {provider['url']}")
            else:
                api_key = input(f"  API key ({provider['key_hint'] or 'paste key'}): ").strip()
                if not api_key:
                    args.no_ai = True
                    print("\n  No key entered. Running local dump.")

            if not args.no_ai:
                args.provider = provider

    # Attach default provider if not set
    if not hasattr(args, 'provider'):
        args.provider = PROVIDERS.get("1") if api_key else None

    if args.no_ai:
        # Raw dump mode — print everything, no AI
        print(f"\n{'='*60}")
        print(f"  FULL MEMORY DUMP — {args.droid}")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"{'='*60}")

        print(f"\n📁 FILES FOUND ({len([f for f in files_found if not f.startswith('_')])})")
        for fname, fdata in files_found.items():
            if fname.startswith('_'):
                continue
            print(f"\n{'─'*50}")
            print(f"  {fname}")
            print(f"  Path: {fdata['path']}")
            print(f"  Modified: {fdata['modified']}")
            print(f"  Size: {fdata['size']} chars")
            print(f"{'─'*50}")
            print(fdata['content'])

        if files_missing:
            print(f"\n⚠️  FILES NOT FOUND ({len(files_missing)}):")
            for f in files_missing:
                print(f"  — {f}")

        identity = files_found.get('_identity_sealed', {})
        if identity:
            print(f"\n🔐 IDENTITY SEALED:")
            for k, v in identity.items():
                print(f"  {k}: {v}")

        skills_count = files_found.get('_skills_count', 0)
        skills_list = files_found.get('_skills_list', [])
        if skills_count:
            print(f"\n📚 SKILLS INSTALLED: {skills_count} files")
            for s in skills_list:
                print(f"  {s}")

        if dag_record:
            print(f"\n🎓 DROIDSCHOOL DAG RECORD:")
            print(json.dumps(dag_record, indent=2))

        # Save the dump
        save_report(args.droid, "# RAW DUMP (no AI analysis)\n\nSee terminal output.", args.workspace)
        return

    # AI analysis
    report = analyze_with_ai(
        args.droid, files_found, files_missing, dag_record, api_key,
        provider=getattr(args, 'provider', None)
    )

    print(f"\n{'='*60}")
    print(f"  ANALYSIS REPORT")
    print(f"{'='*60}\n")
    print(report)

    # Save report
    save_report(args.droid, report, args.workspace)

if __name__ == "__main__":
    main()

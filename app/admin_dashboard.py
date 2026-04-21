"""Internal admin dashboard for runtime settings and manual operations."""

from __future__ import annotations

import hmac
import json
import secrets
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, flash, redirect, render_template_string, request, session, url_for
from werkzeug.utils import secure_filename

from app.config import Config
from app.storage import (
    ProjectConfig,
    RuntimeSettings,
    add_uploaded_knowledge_file,
    clear_operational_data,
    delete_knowledge_source_chunks,
    delete_job_logs,
    delete_summaries,
    get_recent_job_logs,
    get_recent_summaries,
    get_runtime_settings,
    get_summary,
    get_uploaded_knowledge_file,
    list_uploaded_knowledge_files,
    log_job_event,
    save_runtime_settings,
    set_uploaded_knowledge_file_active,
)


ROOT_DIR = Path(__file__).resolve().parent.parent
JOB_RUN_LOG_DIR = ROOT_DIR / ".job_runs"
ALLOWED_KNOWLEDGE_EXTENSIONS = {".md", ".txt", ".pdf"}


BASE_TEMPLATE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ page_title }}</title>
    <style>
      :root {
        --paper: #f5f4ed;
        --ivory: #faf9f5;
        --text: #141413;
        --text-secondary: #5e5d59;
        --text-muted: #87867f;
        --border: #e8e6dc;
        --ring: #d1cfc5;
        --terracotta: #c96442;
        --terracotta-soft: #d97757;
        --focus: #3898ec;
        --ok-bg: #ece8dc;
        --ok-fg: #3d3d3a;
        --warn-bg: #f3e2dc;
        --warn-fg: #7a3b28;
        --error-bg: #f3dddd;
        --error-fg: #8b2b2b;
      }

      * { box-sizing: border-box; }

      body {
        margin: 0;
        background: var(--paper);
        color: var(--text);
        font-family: "Avenir Next", "Segoe UI", Arial, sans-serif;
        line-height: 1.6;
      }

      .shell {
        max-width: 1160px;
        margin: 0 auto;
        padding: 18px 16px 36px;
      }

      .topbar {
        display: flex;
        flex-wrap: wrap;
        justify-content: space-between;
        gap: 10px;
        align-items: center;
        padding: 14px 16px;
        border: 1px solid var(--border);
        border-radius: 16px;
        background: var(--ivory);
        box-shadow: 0 0 0 1px var(--ring);
        margin-bottom: 16px;
      }

      .brand h1 {
        margin: 0;
        font-family: Georgia, "Times New Roman", serif;
        font-size: clamp(1.5rem, 3.4vw, 2.15rem);
        line-height: 1.18;
        font-weight: 500;
        letter-spacing: 0;
      }

      .brand .subtitle {
        margin: 2px 0 0;
        font-size: 0.92rem;
        color: var(--text-secondary);
      }

      .nav {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
      }

      .nav a {
        color: var(--text-secondary);
        text-decoration: none;
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 7px 12px;
        background: var(--paper);
        font-size: 0.92rem;
      }

      .nav a.active {
        color: var(--text);
        background: #efece0;
        box-shadow: 0 0 0 1px var(--ring);
      }

      .logout {
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 8px 12px;
        background: #ffffff;
        color: var(--text-secondary);
        cursor: pointer;
      }

      .flash-stack { margin-bottom: 14px; }

      .flash {
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 10px 12px;
        margin-bottom: 8px;
        font-size: 0.92rem;
      }

      .flash-success { background: var(--ok-bg); color: var(--ok-fg); }
      .flash-error { background: var(--error-bg); color: var(--error-fg); }

      .card {
        background: var(--ivory);
        border: 1px solid var(--border);
        border-radius: 16px;
        padding: 18px;
        margin-bottom: 14px;
        box-shadow: 0 0 0 1px rgba(209, 207, 197, 0.32);
      }

      .section-title {
        margin: 0 0 6px;
        font-family: Georgia, "Times New Roman", serif;
        font-size: clamp(1.2rem, 2.2vw, 1.7rem);
        line-height: 1.22;
        font-weight: 500;
      }

      .section-note {
        margin: 0 0 12px;
        color: var(--text-secondary);
        font-size: 0.92rem;
      }

      .mini-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
        gap: 8px;
      }

      .metric {
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 8px 10px;
        background: #fff;
      }

      .metric .label {
        color: var(--text-muted);
        font-size: 0.74rem;
        letter-spacing: 0.2px;
        text-transform: uppercase;
      }

      .metric .value {
        margin-top: 2px;
        font-weight: 600;
        color: var(--text);
        font-size: 0.95rem;
      }

      .settings-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 10px;
      }

      .field {
        border: 1px solid var(--border);
        border-radius: 12px;
        background: #fff;
        padding: 10px;
      }

      label {
        display: block;
        margin-bottom: 4px;
        font-size: 0.8rem;
        font-weight: 600;
        color: var(--text-secondary);
        letter-spacing: 0.1px;
      }

      .hint {
        margin-top: 4px;
        color: var(--text-muted);
        font-size: 0.76rem;
        line-height: 1.45;
      }

      input {
        width: 100%;
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 8px 10px;
        font: inherit;
        color: var(--text);
        background: #fff;
      }
      textarea {
        width: 100%;
        min-height: 160px;
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 8px 10px;
        font: inherit;
        color: var(--text);
        background: #fff;
        resize: vertical;
      }

      input:focus, textarea:focus {
        outline: none;
        border-color: var(--focus);
        box-shadow: 0 0 0 2px rgba(56, 152, 236, 0.15);
      }

      .inline-toggle {
        display: flex;
        align-items: center;
        gap: 8px;
      }

      .inline-toggle input[type='checkbox'] {
        width: auto;
        margin: 0;
      }

      .btn-primary {
        border: 0;
        border-radius: 12px;
        padding: 9px 14px;
        background: var(--terracotta);
        color: var(--ivory);
        cursor: pointer;
        font-weight: 600;
      }

      .btn-primary:hover { background: var(--terracotta-soft); }
      .btn-secondary {
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 9px 14px;
        background: #fff;
        color: var(--text);
        cursor: pointer;
        font-weight: 600;
      }
      .btn-danger {
        border: 1px solid #d7b6ac;
        border-radius: 12px;
        padding: 8px 12px;
        background: #fff0ec;
        color: #8a3a24;
        cursor: pointer;
        font-weight: 600;
      }

      .row {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
      }

      .action-card {
        flex: 1 1 300px;
        border: 1px solid var(--border);
        border-radius: 14px;
        background: #fff;
        padding: 12px;
      }

      .action-card h3 {
        margin: 0 0 4px;
        font-family: Georgia, "Times New Roman", serif;
        font-size: 1.1rem;
        font-weight: 500;
      }

      .action-card p {
        margin: 0 0 10px;
        color: var(--text-secondary);
        font-size: 0.88rem;
      }

      table {
        width: 100%;
        border-collapse: separate;
        border-spacing: 0;
        font-size: 0.91rem;
        background: #fff;
        border: 1px solid var(--border);
        border-radius: 12px;
        overflow: hidden;
      }

      th, td {
        text-align: left;
        border-bottom: 1px solid var(--border);
        padding: 10px 9px;
        vertical-align: top;
      }

      th {
        font-size: 0.75rem;
        color: var(--text-muted);
        text-transform: uppercase;
        letter-spacing: 0.2px;
        background: #f8f7f2;
      }

      tbody tr:last-child td { border-bottom: 0; }
      tbody tr:nth-child(even) td { background: #fcfbf8; }

      .summary-final {
        max-width: 520px;
        color: var(--text-secondary);
      }

      .badge {
        display: inline-flex;
        align-items: center;
        border-radius: 999px;
        padding: 3px 9px;
        font-size: 0.74rem;
        font-weight: 600;
      }

      .badge-posted { background: var(--ok-bg); color: var(--ok-fg); }
      .badge-pending { background: var(--warn-bg); color: var(--warn-fg); }
      .badge-success { background: var(--ok-bg); color: var(--ok-fg); }
      .badge-failed { background: var(--error-bg); color: var(--error-fg); }
      .badge-started, .badge-queued, .badge-skipped { background: #efece0; color: #4d4c48; }
      .badge-finished { background: var(--ok-bg); color: var(--ok-fg); }
      .badge-progress { background: #efece0; color: #4d4c48; }
      .badge-degraded { background: var(--warn-bg); color: var(--warn-fg); }

      a.link {
        color: var(--text);
        text-decoration: none;
        border-bottom: 1px solid transparent;
      }

      a.link:hover { border-bottom-color: var(--text-secondary); }
      .row-action {
        white-space: nowrap;
      }
      .view-btn {
        display: inline-block;
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 4px 10px;
        text-decoration: none;
        color: var(--text);
        background: #fff;
        font-size: 0.82rem;
        font-weight: 600;
      }
      .view-btn:hover {
        background: #f3f1e8;
      }

      .back-link {
        color: var(--text-secondary);
        text-decoration: none;
        font-size: 0.9rem;
      }

      .list {
        margin: 0;
        padding-left: 18px;
      }

      .list li { margin-bottom: 6px; }
      .mono {
        font-family: "SFMono-Regular", Menlo, Consolas, monospace;
        font-size: 0.8rem;
        color: var(--text-secondary);
      }

      @media (max-width: 760px) {
        .shell { padding-top: 12px; }
        .topbar { border-radius: 12px; }
        .card { border-radius: 12px; padding: 14px; }
        th, td { padding: 8px 7px; }
      }
    </style>
  </head>
  <body>
    <main class="shell">
      <header class="topbar">
        <div class="brand">
          <h1>QA Reminder Admin</h1>
          <p class="subtitle">Internal control panel for daily QA summary workflow</p>
        </div>
        <nav class="nav">
          <a href="{{ url_for('admin_home') }}" class="{{ 'active' if active_nav == 'dashboard' else '' }}">Dashboard</a>
          <a href="{{ url_for('manual_actions') }}" class="{{ 'active' if active_nav == 'actions' else '' }}">Manual Actions</a>
          <a href="{{ url_for('job_logs') }}" class="{{ 'active' if active_nav == 'logs' else '' }}">Job Logs</a>
        </nav>
        <form method="post" action="{{ url_for('admin_logout') }}">
          <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
          <button class="logout" type="submit">Log out</button>
        </form>
      </header>

      {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}
      <section class="flash-stack">
        {% for category, message in messages %}
          <div class="flash {{ 'flash-success' if category == 'success' else 'flash-error' }}">{{ message }}</div>
        {% endfor %}
      </section>
      {% endif %}
      {% endwith %}

      {{ body_content | safe }}
    </main>
  </body>
</html>
"""


DASHBOARD_CONTENT_TEMPLATE = """
<section class="card">
  <h2 class="section-title">Effective Configuration</h2>
  <p class="section-note">Current runtime values from database and secret availability from environment.</p>
  <div class="mini-grid">
    <div class="metric"><div class="label">Timezone</div><div class="value">{{ effective.timezone }}</div></div>
    <div class="metric"><div class="label">Nightly Slot</div><div class="value">{{ effective.nightly_at }}</div></div>
    <div class="metric"><div class="label">Morning Slot</div><div class="value">{{ effective.morning_at }}</div></div>
    <div class="metric"><div class="label">Projects</div><div class="value">{{ effective.project_count }}</div></div>
    <div class="metric"><div class="label">Designated Role</div><div class="value">{{ effective.designated_role_id or 'Not set' }}</div></div>
    <div class="metric"><div class="label">Shared Knowledge Channels</div><div class="value">{{ effective.shared_knowledge_channel_count }}</div></div>
    <div class="metric"><div class="label">Shared Knowledge Files</div><div class="value">{{ effective.shared_knowledge_file_count }}</div></div>
    <div class="metric"><div class="label">Dry Run</div><div class="value">{{ 'Enabled' if effective.dry_run else 'Disabled' }}</div></div>
    <div class="metric"><div class="label">Discord Token</div><div class="value">{{ 'Loaded' if effective.discord_token_loaded else 'Missing' }}</div></div>
    <div class="metric"><div class="label">Gemini Key</div><div class="value">{{ 'Loaded' if effective.gemini_key_loaded else 'Missing' }}</div></div>
  </div>
  <p class="section-note" style="margin-top:10px;">Database path: {{ database_path }}</p>
</section>

<section class="card">
  <h2 class="section-title">Runtime Settings</h2>
  <p class="section-note">Use this form for operational changes. Saved values apply to scheduler and manual runs.</p>
  <form method="post" action="{{ url_for('save_settings') }}">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <div class="settings-grid">
      <div class="field">
        <label>Designated Role ID (optional)</label>
        <input name="designated_role_id" value="{{ settings.designated_role_id or '' }}" placeholder="Role ping shown before reminder">
        <div class="hint">Prepends &lt;@&ROLE_ID&gt; before the reminder. Project JSON can override this per project.</div>
      </div>
      <div class="field">
        <label>Shared Knowledge Channel IDs (optional)</label>
        <input name="shared_knowledge_channel_ids" value="{{ settings.shared_knowledge_channel_ids | join(',') }}" placeholder="111,222">
        <div class="hint">Common guideline/reference channels used by all projects during knowledge ingestion.</div>
      </div>
      <div class="field">
        <label>Shared Knowledge File Paths (optional)</label>
        <input name="shared_knowledge_file_paths" value="{{ settings.shared_knowledge_file_paths | join(',') }}" placeholder="/path/a.md,/path/b.pdf">
        <div class="hint">Comma-separated local file paths (MD/TXT/PDF) shared across projects.</div>
      </div>
      <div class="field">
        <label>Timezone (IANA)</label>
        <input name="timezone" value="{{ settings.timezone }}" placeholder="Asia/Bangkok" required>
        <div class="hint">Used for schedule matching and day windows.</div>
      </div>
      <div class="field">
        <label>Nightly Summary Hour (0-23)</label>
        <input name="nightly_summary_hour" value="{{ settings.nightly_summary_hour }}" required>
      </div>
      <div class="field">
        <label>Nightly Summary Minute (0-59)</label>
        <input name="nightly_summary_minute" value="{{ settings.nightly_summary_minute }}" required>
      </div>
      <div class="field">
        <label>Morning Post Hour (0-23)</label>
        <input name="morning_post_hour" value="{{ settings.morning_post_hour }}" required>
      </div>
      <div class="field">
        <label>Morning Post Minute (0-59)</label>
        <input name="morning_post_minute" value="{{ settings.morning_post_minute }}" required>
      </div>
      <div class="field">
        <label>Dry Run</label>
        <div class="inline-toggle">
          <input type="checkbox" name="dry_run" value="1" {% if settings.dry_run %}checked{% endif %}>
          <span>Enable preview mode (no real reminder post)</span>
        </div>
      </div>
      <div class="field">
        <label>Dry Run Channel ID (optional)</label>
        <input name="dry_run_channel_id" value="{{ settings.dry_run_channel_id or '' }}" placeholder="Optional test channel">
      </div>
      <div class="field">
        <label>Allowed User IDs (optional)</label>
        <input name="allowed_user_ids" value="{{ settings.allowed_user_ids | join(',') }}" placeholder="111,222">
      </div>
      <div class="field">
        <label>Allowed Role IDs (optional)</label>
        <input name="allowed_role_ids" value="{{ settings.allowed_role_ids | join(',') }}" placeholder="333,444">
      </div>
      <div class="field" style="grid-column: 1 / -1;">
        <label>Project Routing JSON (required)</label>
        <textarea name="project_configs_json" placeholder='[{"key":"project-a","name":"Project A","source_channel_ids":[123],"post_channel_id":456,"mention_role_id":789,"knowledge_channel_ids":[321],"knowledge_file_paths":["/path/guide.md"]}]'>{{ project_configs_json }}</textarea>
        <div class="hint">Project-routing only. Each item: key, name, source_channel_ids, post_channel_id or fallback_post_channel_id, optional mention_role_id, optional knowledge_channel_ids, optional knowledge_file_paths.</div>
      </div>
    </div>
    <div style="margin-top:12px;">
      <button class="btn-primary" type="submit">Save Runtime Settings</button>
    </div>
  </form>
</section>

<section class="card">
  <h2 class="section-title">Summary History</h2>
  <p class="section-note">Click a date to view full detail.</p>
  <table>
    <thead>
      <tr>
        <th>Date</th>
        <th>Project</th>
        <th>Detail</th>
        <th>Post Status</th>
        <th>Highlights</th>
        <th>Blockers</th>
        <th>Follow-ups</th>
        <th>Final Message</th>
      </tr>
    </thead>
    <tbody>
      {% for row in summaries %}
      <tr>
        <td><a class="link" href="{{ url_for('summary_detail', summary_date=row.date, project_key=row.project_key) }}">{{ row.date }}</a></td>
        <td>{{ row.project_name }}</td>
        <td class="row-action">
          <a class="view-btn" href="{{ url_for('summary_detail', summary_date=row.date, project_key=row.project_key) }}">View</a>
        </td>
        <td>
          {% if row.posted %}
          <span class="badge badge-posted">Posted</span>
          {% else %}
          <span class="badge badge-pending">Pending</span>
          {% endif %}
        </td>
        <td>{{ row.highlights | length }}</td>
        <td>{{ row.blockers | length }}</td>
        <td>{{ row.follow_ups | length }}</td>
        <td class="summary-final">{{ row.final_message or 'No final message' }}</td>
      </tr>
      {% else %}
      <tr>
        <td colspan="8">No summaries saved yet.</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</section>
"""


ACTIONS_CONTENT_TEMPLATE = """
<section class="card">
  <h2 class="section-title">Manual Job Actions</h2>
  <p class="section-note">Use for one-off reruns. Jobs run in background using existing bot logic and current runtime settings (all configured projects).</p>
  <div class="row">
    <div class="action-card">
      <h3>Run Nightly Summary</h3>
      <p>Collect, filter, summarize, and save for the selected date. Empty date means previous local day.</p>
      <form method="post" action="{{ url_for('run_nightly') }}">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <label>Target Date (optional)</label>
        <input name="date" placeholder="YYYY-MM-DD">
        <div style="margin-top:10px;"><button class="btn-primary" type="submit">Start Nightly Job</button></div>
      </form>
    </div>

    <div class="action-card">
      <h3>Run Morning Reminder</h3>
      <p>Load summary and perform normal post behavior (or dry-run preview if enabled).</p>
      <form method="post" action="{{ url_for('run_morning') }}">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <label>Target Date (optional)</label>
        <input name="date" placeholder="YYYY-MM-DD">
        <div style="margin-top:10px;"><button class="btn-primary" type="submit">Start Morning Job</button></div>
      </form>
    </div>
  </div>
</section>

<section class="card">
  <h2 class="section-title">Project Knowledge Manager</h2>
  <p class="section-note">Upload project files from browser, then ingest using existing knowledge pipeline.</p>
  <div class="action-card">
    <h3>Project Selection</h3>
    <form method="get" action="{{ url_for('manual_actions') }}">
      <label>Project</label>
      <select name="project_key" style="width:100%; border:1px solid var(--border); border-radius:10px; padding:8px 10px; background:#fff;">
        {% for item in project_options %}
        <option value="{{ item.key }}" {% if item.key == selected_project_key %}selected{% endif %}>{{ item.name }} ({{ item.key }})</option>
        {% endfor %}
      </select>
      <div style="margin-top:10px;"><button class="btn-secondary" type="submit">Load Project Files</button></div>
    </form>
  </div>
  <div class="action-card" style="margin-top:10px;">
    <h3>Upload Files</h3>
    <p>Supported: .md, .txt, .pdf</p>
    <form method="post" action="{{ url_for('upload_knowledge_files') }}" enctype="multipart/form-data">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <input type="hidden" name="project_key" value="{{ selected_project_key }}">
      <label>Files</label>
      <input type="file" name="knowledge_files" multiple required>
      <div style="margin-top:10px;" class="row">
        <button class="btn-secondary" type="submit" name="post_upload_action" value="upload_only">Upload Only</button>
        <button class="btn-primary" type="submit" name="post_upload_action" value="upload_and_ingest">Upload + Ingest</button>
      </div>
    </form>
  </div>
  <div class="action-card" style="margin-top:10px;">
    <h3>Ingestion Actions</h3>
    <p>Run ingestion for selected project sources (channels + JSON paths + dashboard-uploaded files).</p>
    <form method="post" action="{{ url_for('reingest_project_knowledge') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <input type="hidden" name="project_key" value="{{ selected_project_key }}">
      <button class="btn-primary" type="submit">Re-ingest All Project Knowledge</button>
    </form>
  </div>
  <div style="margin-top:12px;">
    <table>
      <thead>
        <tr>
          <th>File</th>
          <th>Type</th>
          <th>Size</th>
          <th>Uploaded</th>
          <th>Status</th>
          <th>Path</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody>
        {% for file in uploaded_files %}
        <tr>
          <td>{{ file.original_filename }}</td>
          <td>{{ file.file_type or '-' }}</td>
          <td>{{ file.file_size_label }}</td>
          <td>{{ file.uploaded_at }}</td>
          <td><span class="badge badge-{{ file.badge_class }}">{{ file.ingest_status }}</span></td>
          <td class="mono">{{ file.stored_path }}</td>
          <td class="row-action">
            <form method="post" action="{{ url_for('detach_knowledge_file') }}" onsubmit="return confirm('Detach this file from project and remove its chunks?');">
              <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
              <input type="hidden" name="file_id" value="{{ file.id }}">
              <input type="hidden" name="project_key" value="{{ selected_project_key }}">
              <button class="btn-danger" type="submit">Detach</button>
            </form>
          </td>
        </tr>
        {% else %}
        <tr>
          <td colspan="7">No uploaded files for this project.</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</section>

<section class="card">
  <h2 class="section-title">Data Cleanup</h2>
  <p class="section-note">Delete job history, summaries, or reset operational data. Date range uses inclusive YYYY-MM-DD.</p>

  <div class="row">
    <div class="action-card">
      <h3>Delete Job History</h3>
      <p>Clear all job logs or only logs in a date range.</p>
      <form method="post" action="{{ url_for('cleanup_job_logs') }}">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <label>Date From (optional)</label>
        <input name="date_from" placeholder="YYYY-MM-DD">
        <label style="margin-top:8px;">Date To (optional)</label>
        <input name="date_to" placeholder="YYYY-MM-DD">
        <div style="margin-top:10px;" class="row">
          <button class="btn-primary" type="submit">Delete Logs (Range/All)</button>
        </div>
      </form>
    </div>

    <div class="action-card">
      <h3>Delete Summaries</h3>
      <p>Clear all summaries or only summaries in a date range.</p>
      <form method="post" action="{{ url_for('cleanup_summaries') }}">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <label>Date From (optional)</label>
        <input name="date_from" placeholder="YYYY-MM-DD">
        <label style="margin-top:8px;">Date To (optional)</label>
        <input name="date_to" placeholder="YYYY-MM-DD">
        <div style="margin-top:10px;" class="row">
          <button class="btn-primary" type="submit">Delete Summaries (Range/All)</button>
        </div>
      </form>
    </div>
  </div>

  <div class="action-card" style="margin-top:10px;">
    <h3>Clear Entire Operational DB Data</h3>
    <p>Deletes all summaries, all job logs, and scheduler run-claims. Runtime settings are kept.</p>
    <form method="post" action="{{ url_for('cleanup_all_data') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <label>Type DELETE to confirm</label>
      <input name="confirm_text" placeholder="DELETE" required>
      <div style="margin-top:10px;">
        <button class="btn-primary" type="submit">Clear All Operational Data</button>
      </div>
    </form>
  </div>
</section>
"""


SUMMARY_DETAIL_CONTENT_TEMPLATE = """
<section class="card">
  <a class="back-link" href="{{ url_for('admin_home') }}">Back to Dashboard</a>
  <h2 class="section-title" style="margin-top:8px;">Summary Detail: {{ summary.date }}</h2>
  <p class="section-note">Project: {{ summary.project_name }} ({{ summary.project_key }}) | Created at {{ summary.created_at }} | {{ 'Posted' if summary.posted else 'Not posted' }}</p>
</section>

<section class="card">
  <h3 class="section-title" style="font-size:1.2rem;">Final Status</h3>
  <p style="margin:0;">{{ summary.final_message or 'No final status message.' }}</p>
</section>

<section class="card">
  <h3 class="section-title" style="font-size:1.2rem;">Highlights</h3>
  <ul class="list">{% for item in summary.highlights %}<li>{{ item }}</li>{% else %}<li>No highlights.</li>{% endfor %}</ul>
</section>

<section class="card">
  <h3 class="section-title" style="font-size:1.2rem;">Blockers</h3>
  <ul class="list">{% for item in summary.blockers %}<li>{{ item }}</li>{% else %}<li>No blockers.</li>{% endfor %}</ul>
</section>

<section class="card">
  <h3 class="section-title" style="font-size:1.2rem;">Follow-ups</h3>
  <ul class="list">{% for item in summary.follow_ups %}<li>{{ item }}</li>{% else %}<li>No follow-ups.</li>{% endfor %}</ul>
</section>
"""


LOGS_CONTENT_TEMPLATE = """
<section class="card">
  <h2 class="section-title">Job Logs</h2>
  <p class="section-note">Recent execution events (UTC timestamps).</p>
  <table>
    <thead>
      <tr>
        <th>Time</th>
        <th>Job</th>
        <th>Trigger</th>
        <th>Target Date</th>
        <th>Status</th>
        <th>Message</th>
      </tr>
    </thead>
    <tbody>
      {% for row in logs %}
      <tr>
        <td>{{ row.created_at }}</td>
        <td>{{ row.job_name }}</td>
        <td>{{ row.trigger_source }}</td>
        <td>{{ row.target_date or '' }}</td>
        <td><span class="badge badge-{{ row.status }}">{{ row.status }}</span></td>
        <td>{{ row.message or '' }}</td>
      </tr>
      {% else %}
      <tr>
        <td colspan="6">No logs yet.</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</section>
"""


LOGIN_TEMPLATE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Admin Login</title>
    <style>
      :root {
        --paper: #f5f4ed;
        --ivory: #faf9f5;
        --text: #141413;
        --secondary: #5e5d59;
        --border: #e8e6dc;
        --ring: #d1cfc5;
        --terracotta: #c96442;
        --focus: #3898ec;
      }
      *, *::before, *::after { box-sizing: border-box; }
      body {
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        padding: 16px;
        font-family: "Avenir Next", "Segoe UI", Arial, sans-serif;
        background: var(--paper);
        color: var(--text);
      }
      .card {
        width: min(100%, 560px);
        padding: 26px;
        border-radius: 18px;
        border: 1px solid var(--border);
        background: var(--ivory);
        box-shadow: 0 0 0 1px var(--ring);
      }
      h2 {
        margin: 0 0 8px;
        font-family: Georgia, "Times New Roman", serif;
        font-size: 2rem;
        line-height: 1.15;
        font-weight: 500;
      }
      p { margin: 0 0 12px; color: var(--secondary); font-size: 0.92rem; }
      .error { margin: 0 0 10px; color: #8b2b2b; font-size: 0.9rem; }
      label { display: block; margin-bottom: 6px; font-size: 0.84rem; color: var(--secondary); font-weight: 600; }
      form { margin-top: 8px; }
      input {
        width: 100%;
        border: 1px solid #d8d5c9;
        border-radius: 12px;
        padding: 11px 12px;
        margin-bottom: 12px;
        background: #ffffff;
        color: var(--text);
      }
      input:focus {
        outline: none;
        border-color: var(--focus);
        box-shadow: 0 0 0 2px rgba(56, 152, 236, 0.14);
      }
      button {
        width: 100%;
        border: 0;
        border-radius: 12px;
        background: var(--terracotta);
        color: #faf9f5;
        padding: 11px;
        font-weight: 600;
        cursor: pointer;
      }
      button:hover { filter: brightness(1.03); }
      @media (max-width: 640px) {
        .card {
          width: min(100%, 460px);
          padding: 20px;
          border-radius: 14px;
        }
        h2 { font-size: 1.8rem; }
      }
    </style>
  </head>
  <body>
    <div class="card">
      <h2>Admin Login</h2>
      <p>Enter admin password to access dashboard controls.</p>
      {% if error %}<div class="error">{{ error }}</div>{% endif %}
      <form method="post">
        <label>Password</label>
        <input type="password" name="password" required autofocus>
        <button type="submit">Sign in</button>
      </form>
    </div>
  </body>
</html>
"""


def create_admin_app(config: Config) -> Flask:
    """Build and configure the Flask admin app."""

    app = Flask(__name__)
    app.secret_key = config.admin_session_secret
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    def _require_auth(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not session.get("admin_authenticated"):
                return redirect(url_for("admin_login_page"))
            return func(*args, **kwargs)

        return wrapper

    def _require_csrf(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            expected = session.get("csrf_token", "")
            provided = request.form.get("csrf_token", "")
            if not expected or not provided or not hmac.compare_digest(str(expected), str(provided)):
                flash("Invalid CSRF token. Please retry.", "error")
                return redirect(url_for("admin_home"))
            return func(*args, **kwargs)

        return wrapper

    def _render_page(
        *,
        page_title: str,
        active_nav: str,
        body_template: str,
        **context: Any,
    ) -> str:
        csrf_token = session.get("csrf_token", "")
        body_content = render_template_string(
            body_template,
            csrf_token=csrf_token,
            **context,
        )
        return render_template_string(
            BASE_TEMPLATE,
            page_title=page_title,
            active_nav=active_nav,
            csrf_token=csrf_token,
            body_content=body_content,
        )

    @app.get("/admin/login")
    def admin_login_page() -> str:
        return render_template_string(LOGIN_TEMPLATE, error=None)

    @app.post("/admin/login")
    def admin_login() -> Any:
        password = request.form.get("password", "")
        if hmac.compare_digest(password, config.admin_password):
            session["admin_authenticated"] = True
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(url_for("admin_home"))
        return render_template_string(LOGIN_TEMPLATE, error="Invalid password"), 401

    @app.post("/admin/logout")
    @_require_auth
    @_require_csrf
    def admin_logout() -> Any:
        session.clear()
        return redirect(url_for("admin_login_page"))

    @app.get("/")
    def root_redirect() -> Any:
        return redirect(url_for("admin_home"))

    @app.get("/health")
    def health() -> tuple[str, int]:
        return "ok", 200

    @app.get("/admin/health")
    def admin_health() -> tuple[str, int]:
        return "ok", 200

    @app.get("/admin")
    @_require_auth
    def admin_home() -> str:
        settings = _safe_get_runtime_settings(config.database_path)
        summaries = get_recent_summaries(limit=20, database_path=config.database_path)
        effective = _build_effective_config(config, settings)
        project_configs_json = _project_configs_to_json(settings.project_configs)
        return _render_page(
            page_title="Dashboard",
            active_nav="dashboard",
            body_template=DASHBOARD_CONTENT_TEMPLATE,
            settings=settings,
            summaries=summaries,
            effective=effective,
            project_configs_json=project_configs_json,
            database_path=config.database_path,
        )

    @app.get("/admin/actions")
    @_require_auth
    def manual_actions() -> str:
        settings = _safe_get_runtime_settings(config.database_path)
        project_options = _resolve_projects_for_dashboard(settings)
        selected_project_key = request.args.get("project_key", "").strip()
        if not selected_project_key and project_options:
            selected_project_key = project_options[0].key
        if selected_project_key and all(project.key != selected_project_key for project in project_options):
            selected_project_key = project_options[0].key if project_options else ""
        uploaded_files = _format_uploaded_rows_for_display(
            list_uploaded_knowledge_files(
                project_key=selected_project_key or None,
                only_active=True,
                database_path=config.database_path,
            )
        )
        return _render_page(
            page_title="Manual Actions",
            active_nav="actions",
            body_template=ACTIONS_CONTENT_TEMPLATE,
            settings=settings,
            project_options=project_options,
            selected_project_key=selected_project_key,
            uploaded_files=uploaded_files,
        )

    @app.get("/admin/summaries/<summary_date>/<project_key>")
    @_require_auth
    def summary_detail(summary_date: str, project_key: str) -> Any:
        try:
            _validate_date(summary_date)
        except ValueError:
            flash("Invalid summary date.", "error")
            return redirect(url_for("admin_home"))

        summary = get_summary(summary_date, config.database_path, project_key=project_key)
        if summary is None:
            flash(f"Summary not found for {summary_date} ({project_key}).", "error")
            return redirect(url_for("admin_home"))

        return _render_page(
            page_title=f"Summary {summary_date}",
            active_nav="dashboard",
            body_template=SUMMARY_DETAIL_CONTENT_TEMPLATE,
            summary=summary,
        )

    @app.get("/admin/logs")
    @_require_auth
    def job_logs() -> str:
        logs = get_recent_job_logs(limit=200, database_path=config.database_path)
        return _render_page(
            page_title="Job Logs",
            active_nav="logs",
            body_template=LOGS_CONTENT_TEMPLATE,
            logs=logs,
        )

    @app.post("/admin/settings")
    @_require_auth
    @_require_csrf
    def save_settings() -> Any:
        try:
            settings = _runtime_settings_from_form(request.form)
            save_runtime_settings(settings, config.database_path)
            flash("Settings saved.", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("admin_home"))

    @app.post("/admin/run-nightly")
    @_require_auth
    @_require_csrf
    def run_nightly() -> Any:
        date_input = request.form.get("date", "").strip()
        try:
            _spawn_manual_job(mode="nightly", date_input=date_input)
            flash("Nightly job started in background process.", "success")
        except Exception as exc:
            flash(str(exc), "error")
        return redirect(url_for("manual_actions"))

    @app.post("/admin/run-morning")
    @_require_auth
    @_require_csrf
    def run_morning() -> Any:
        date_input = request.form.get("date", "").strip()
        try:
            _spawn_manual_job(mode="morning", date_input=date_input)
            flash("Morning job started in background process.", "success")
        except Exception as exc:
            flash(str(exc), "error")
        return redirect(url_for("manual_actions"))

    @app.post("/admin/ingest-knowledge")
    @_require_auth
    @_require_csrf
    def ingest_knowledge() -> Any:
        project_key = request.form.get("project_key", "").strip()
        try:
            _spawn_manual_job(mode="ingest-knowledge", date_input="", project_key=project_key or None)
            flash("Knowledge ingestion started in background process.", "success")
        except Exception as exc:
            flash(str(exc), "error")
        return redirect(url_for("manual_actions"))

    @app.post("/admin/knowledge/upload")
    @_require_auth
    @_require_csrf
    def upload_knowledge_files() -> Any:
        project_key = request.form.get("project_key", "").strip()
        if not project_key:
            flash("Project key is required.", "error")
            return redirect(url_for("manual_actions"))

        settings = _safe_get_runtime_settings(config.database_path)
        projects = _resolve_projects_for_dashboard(settings)
        selected_project = next((row for row in projects if row.key == project_key), None)
        if selected_project is None:
            flash("Selected project is not configured.", "error")
            return redirect(url_for("manual_actions", project_key=project_key))

        upload_dir = Path(config.knowledge_upload_dir).expanduser() / project_key
        upload_dir.mkdir(parents=True, exist_ok=True)

        uploaded_files = request.files.getlist("knowledge_files")
        if not uploaded_files:
            flash("Please choose at least one file.", "error")
            return redirect(url_for("manual_actions", project_key=project_key))

        uploaded_count = 0
        for uploaded in uploaded_files:
            original_name = str(uploaded.filename or "").strip()
            if not original_name:
                continue
            extension = Path(original_name).suffix.lower()
            if extension not in ALLOWED_KNOWLEDGE_EXTENSIONS:
                flash(f"Skipped {original_name}: unsupported type {extension or '(none)'}", "error")
                continue
            sanitized = secure_filename(Path(original_name).name)
            if not sanitized:
                sanitized = f"knowledge_{datetime.utcnow().strftime('%Y%m%dT%H%M%S%f')}{extension}"
            stored_name = f"{datetime.utcnow().strftime('%Y%m%dT%H%M%S%f')}_{secrets.token_hex(4)}_{sanitized}"
            stored_path = upload_dir / stored_name
            uploaded.save(stored_path)
            file_size = stored_path.stat().st_size if stored_path.exists() else 0
            add_uploaded_knowledge_file(
                project_key=project_key,
                original_filename=original_name,
                stored_path=str(stored_path),
                file_type=extension.lstrip("."),
                file_size_bytes=file_size,
                uploaded_by="admin",
                database_path=config.database_path,
            )
            uploaded_count += 1

        if uploaded_count == 0:
            flash("No files uploaded. Check file types and input.", "error")
            return redirect(url_for("manual_actions", project_key=project_key))

        post_upload_action = request.form.get("post_upload_action", "upload_only").strip()
        if post_upload_action == "upload_and_ingest":
            try:
                _spawn_manual_job(mode="ingest-knowledge", date_input="", project_key=project_key)
                flash(
                    f"Uploaded {uploaded_count} file(s). Ingestion started for project {project_key}.",
                    "success",
                )
            except Exception as exc:
                flash(f"Uploaded {uploaded_count} file(s), but ingestion start failed: {exc}", "error")
        else:
            flash(f"Uploaded {uploaded_count} file(s) to project {project_key}.", "success")
        return redirect(url_for("manual_actions", project_key=project_key))

    @app.post("/admin/knowledge/reingest-project")
    @_require_auth
    @_require_csrf
    def reingest_project_knowledge() -> Any:
        project_key = request.form.get("project_key", "").strip()
        try:
            _spawn_manual_job(mode="ingest-knowledge", date_input="", project_key=project_key or None)
            flash("Project knowledge ingestion started.", "success")
        except Exception as exc:
            flash(str(exc), "error")
        return redirect(url_for("manual_actions", project_key=project_key))

    @app.post("/admin/knowledge/detach")
    @_require_auth
    @_require_csrf
    def detach_knowledge_file() -> Any:
        project_key = request.form.get("project_key", "").strip()
        raw_id = request.form.get("file_id", "").strip()
        try:
            file_id = int(raw_id)
        except ValueError:
            flash("Invalid file id.", "error")
            return redirect(url_for("manual_actions", project_key=project_key))

        row = get_uploaded_knowledge_file(file_id, database_path=config.database_path)
        if row is None or row.project_key != project_key:
            flash("Knowledge file not found for selected project.", "error")
            return redirect(url_for("manual_actions", project_key=project_key))

        if not set_uploaded_knowledge_file_active(file_id, False, database_path=config.database_path):
            flash("Failed to detach file.", "error")
            return redirect(url_for("manual_actions", project_key=project_key))

        deleted = delete_knowledge_source_chunks(
            project_key=project_key,
            source_type="file",
            source_ref=row.stored_path,
            database_path=config.database_path,
        )
        flash(f"Detached file and removed {deleted} ingested chunk(s).", "success")
        return redirect(url_for("manual_actions", project_key=project_key))

    @app.post("/admin/cleanup/job-logs")
    @_require_auth
    @_require_csrf
    def cleanup_job_logs() -> Any:
        try:
            date_from, date_to = _parse_optional_date_range(
                request.form.get("date_from", "").strip(),
                request.form.get("date_to", "").strip(),
            )
            deleted = delete_job_logs(
                date_from=date_from,
                date_to=date_to,
                database_path=config.database_path,
            )
            flash(f"Deleted {deleted} job log row(s).", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("manual_actions"))

    @app.post("/admin/cleanup/summaries")
    @_require_auth
    @_require_csrf
    def cleanup_summaries() -> Any:
        try:
            date_from, date_to = _parse_optional_date_range(
                request.form.get("date_from", "").strip(),
                request.form.get("date_to", "").strip(),
            )
            deleted = delete_summaries(
                date_from=date_from,
                date_to=date_to,
                database_path=config.database_path,
            )
            flash(f"Deleted {deleted} summary row(s).", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("manual_actions"))

    @app.post("/admin/cleanup/all")
    @_require_auth
    @_require_csrf
    def cleanup_all_data() -> Any:
        if request.form.get("confirm_text", "").strip() != "DELETE":
            flash("Confirmation text must be exactly DELETE.", "error")
            return redirect(url_for("manual_actions"))
        result = clear_operational_data(config.database_path)
        flash(
            (
                f"Cleared operational data: summaries={result['summaries']}, "
                f"job_logs={result['job_logs']}, scheduler_claims={result['scheduler_claims']}."
            ),
            "success",
        )
        return redirect(url_for("manual_actions"))

    def _spawn_manual_job(mode: str, date_input: str, project_key: str | None = None) -> None:
        if mode not in {"nightly", "morning", "ingest-knowledge"}:
            raise ValueError("Unsupported mode.")

        command = [sys.executable, "-m", "app.main", "--mode", mode]
        target_date: str | None = None
        if mode in {"nightly", "morning"} and date_input:
            _validate_date(date_input)
            command.extend(["--date", date_input])
            target_date = date_input
        if mode == "ingest-knowledge" and project_key:
            command.extend(["--project-key", project_key])

        JOB_RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
        run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%S_%f")
        output_path = JOB_RUN_LOG_DIR / f"{run_id}_{mode}.log"
        output_handle = output_path.open("a", encoding="utf-8")

        process = subprocess.Popen(
            command,
            cwd=str(ROOT_DIR),
            stdout=output_handle,
            stderr=subprocess.STDOUT,
        )
        try:
            return_code = process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            threading.Thread(
                target=_watch_manual_job_process,
                args=(
                    process,
                    output_handle,
                    output_path,
                    mode,
                    target_date,
                    config.database_path,
                ),
                daemon=True,
            ).start()
            log_job_event(
                job_name=mode,
                trigger_source="dashboard",
                status="queued",
                target_date=target_date,
                message=(
                    f"Manual run queued (pid={process.pid}). "
                    f"Output: {output_path}"
                ),
                database_path=config.database_path,
            )
            return
        output_handle.close()
        error_tail = _tail_file(output_path)
        if error_tail:
            log_job_event(
                job_name=mode,
                trigger_source="dashboard",
                status="failed",
                target_date=target_date,
                message=(
                    f"Process exited immediately with code {return_code}. "
                    f"Output: {output_path}. Tail: {error_tail}"
                ),
                database_path=config.database_path,
            )
            raise ValueError(
                f"Job process exited immediately with code {return_code}. "
                f"Check output log: {output_path}"
            )
        raise ValueError(
            f"Job process exited immediately with code {return_code}. "
            "Please check runtime configuration."
        )

    return app


def _safe_get_runtime_settings(database_path: str) -> RuntimeSettings:
    """Get runtime settings or fallback defaults when settings are not initialized yet."""

    try:
        return get_runtime_settings(database_path)
    except (sqlite3.Error, ValueError):
        return RuntimeSettings(
            source_channel_ids=[],
            reminder_channel_id=0,
            timezone="Asia/Bangkok",
            nightly_summary_hour=0,
            nightly_summary_minute=5,
            morning_post_hour=9,
            morning_post_minute=0,
            dry_run=False,
            dry_run_channel_id=None,
            allowed_user_ids=[],
            allowed_role_ids=[],
            designated_role_id=None,
            shared_post_channel_id=None,
            shared_knowledge_channel_ids=[],
            shared_knowledge_file_paths=[],
            project_configs=[],
        )


def _build_effective_config(config: Config, settings: RuntimeSettings) -> dict[str, Any]:
    """Build effective runtime/env display for dashboard."""

    return {
        "timezone": settings.timezone,
        "nightly_at": f"{settings.nightly_summary_hour:02d}:{settings.nightly_summary_minute:02d}",
        "morning_at": f"{settings.morning_post_hour:02d}:{settings.morning_post_minute:02d}",
        "project_count": len(settings.project_configs) if settings.project_configs else 1,
        "designated_role_id": settings.designated_role_id,
        "shared_knowledge_channel_count": len(settings.shared_knowledge_channel_ids),
        "shared_knowledge_file_count": len(settings.shared_knowledge_file_paths),
        "dry_run": settings.dry_run,
        "discord_token_loaded": bool(config.discord_bot_token),
        "gemini_key_loaded": bool(config.gemini_api_key),
    }


def _resolve_projects_for_dashboard(settings: RuntimeSettings) -> list[ProjectConfig]:
    """Resolve projects for admin pages, preserving legacy single-project fallback."""

    if settings.project_configs:
        return settings.project_configs
    return [
        ProjectConfig(
            key="default",
            name="Default",
            source_channel_ids=settings.source_channel_ids,
            post_channel_id=settings.reminder_channel_id if settings.reminder_channel_id > 0 else None,
            fallback_post_channel_id=settings.shared_post_channel_id,
            mention_role_id=settings.designated_role_id,
            knowledge_channel_ids=[],
            knowledge_file_paths=[],
        )
    ]


def _format_uploaded_rows_for_display(rows: list[Any]) -> list[dict[str, Any]]:
    """Prepare uploaded knowledge metadata rows for dashboard table rendering."""

    formatted: list[dict[str, Any]] = []
    for row in rows:
        formatted.append(
            {
                "id": row.id,
                "project_key": row.project_key,
                "original_filename": row.original_filename,
                "stored_path": row.stored_path,
                "file_type": row.file_type,
                "file_size_label": _format_file_size(row.file_size_bytes),
                "uploaded_at": row.uploaded_at,
                "ingest_status": row.ingest_status,
                "badge_class": _ingest_status_badge_class(row.ingest_status),
            }
        )
    return formatted


def _format_file_size(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{int(size_bytes)} B"


def _ingest_status_badge_class(status: str) -> str:
    value = status.strip().lower()
    if value in {"ingested", "success"}:
        return "success"
    if value in {"failed", "error"}:
        return "failed"
    if value in {"empty", "pending"}:
        return "degraded"
    return "queued"


def _runtime_settings_from_form(form: Any) -> RuntimeSettings:
    """Parse and validate settings form input."""

    timezone_name = str(form.get("timezone", "")).strip()
    _validate_timezone(timezone_name)

    allowed_user_ids = _parse_csv_ints(str(form.get("allowed_user_ids", "")), required=False)
    allowed_role_ids = _parse_csv_ints(str(form.get("allowed_role_ids", "")), required=False)
    project_configs = _parse_project_configs_json(str(form.get("project_configs_json", "")))
    shared_knowledge_channel_ids = _parse_csv_ints(
        str(form.get("shared_knowledge_channel_ids", "")).strip(),
        required=False,
    )
    shared_knowledge_file_paths = _parse_path_list(
        str(form.get("shared_knowledge_file_paths", "")).strip(),
    )
    designated_role_id = _parse_optional_positive_int(
        str(form.get("designated_role_id", "")).strip(),
        "Designated role ID",
    )

    _validate_positive_unique_ids(allowed_user_ids, "Allowed user IDs")
    _validate_positive_unique_ids(allowed_role_ids, "Allowed role IDs")
    if not project_configs:
        raise ValueError("Project Routing JSON is required.")
    for project in project_configs:
        if project.post_channel_id is None and project.fallback_post_channel_id is None:
            raise ValueError(
                f"Project {project.key} must include post_channel_id or fallback_post_channel_id."
            )

    dry_run = form.get("dry_run") == "1"
    dry_run_channel_id = _parse_optional_positive_int(
        str(form.get("dry_run_channel_id", "")).strip(),
        "Dry run channel ID",
    )
    if dry_run and dry_run_channel_id is not None:
        project_post_channels = {
            channel_id
            for project in project_configs
            for channel_id in (project.post_channel_id, project.fallback_post_channel_id)
            if channel_id is not None
        }
        if dry_run_channel_id in project_post_channels:
            raise ValueError("Dry run channel must be different from project posting channels.")

    return RuntimeSettings(
        source_channel_ids=[],
        reminder_channel_id=0,
        timezone=timezone_name,
        nightly_summary_hour=_parse_ranged_int(str(form.get("nightly_summary_hour", "")), "Nightly summary hour", 0, 23),
        nightly_summary_minute=_parse_ranged_int(str(form.get("nightly_summary_minute", "")), "Nightly summary minute", 0, 59),
        morning_post_hour=_parse_ranged_int(str(form.get("morning_post_hour", "")), "Morning post hour", 0, 23),
        morning_post_minute=_parse_ranged_int(str(form.get("morning_post_minute", "")), "Morning post minute", 0, 59),
        dry_run=dry_run,
        dry_run_channel_id=dry_run_channel_id,
        allowed_user_ids=allowed_user_ids,
        allowed_role_ids=allowed_role_ids,
        designated_role_id=designated_role_id,
        shared_post_channel_id=None,
        shared_knowledge_channel_ids=shared_knowledge_channel_ids,
        shared_knowledge_file_paths=shared_knowledge_file_paths,
        project_configs=project_configs,
    )


def _validate_timezone(value: str) -> None:
    if not value:
        raise ValueError("Timezone is required.")
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Invalid timezone: {value!r}") from exc


def _parse_csv_ints(raw: str, required: bool) -> list[int]:
    cleaned = [item.strip() for item in raw.split(",") if item.strip()]
    if required and not cleaned:
        raise ValueError("At least one source channel ID is required.")
    try:
        return [int(item) for item in cleaned]
    except ValueError as exc:
        raise ValueError("List must contain only integer IDs.") from exc


def _validate_positive_unique_ids(values: list[int], label: str) -> None:
    if any(value <= 0 for value in values):
        raise ValueError(f"{label} must contain only positive IDs.")
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must not contain duplicates.")


def _parse_positive_int(raw: str, label: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{label} must be an integer.") from exc
    if value <= 0:
        raise ValueError(f"{label} must be positive.")
    return value


def _parse_optional_positive_int(raw: str, label: str) -> int | None:
    if not raw:
        return None
    return _parse_positive_int(raw, label)


def _project_configs_to_json(project_configs: list[ProjectConfig]) -> str:
    """Format project configs as stable JSON for dashboard textarea."""

    if not project_configs:
        return "[]"
    return json.dumps(
        [
            {
                "key": row.key,
                "name": row.name,
                "source_channel_ids": row.source_channel_ids,
                "post_channel_id": row.post_channel_id,
                "fallback_post_channel_id": row.fallback_post_channel_id,
                "mention_role_id": row.mention_role_id,
                "knowledge_channel_ids": row.knowledge_channel_ids,
                "knowledge_file_paths": row.knowledge_file_paths,
            }
            for row in project_configs
        ],
        indent=2,
        ensure_ascii=True,
    )


def _parse_project_configs_json(raw: str) -> list[ProjectConfig]:
    """Parse and validate project routing JSON from the dashboard form."""

    if not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Project Routing JSON is invalid: {exc.msg}.") from exc
    if not isinstance(parsed, list):
        raise ValueError("Project Routing JSON must be an array of project objects.")

    items: list[ProjectConfig] = []
    seen_keys: set[str] = set()
    for index, row in enumerate(parsed, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Project entry #{index} must be an object.")
        key = str(row.get("key", "")).strip()
        name = str(row.get("name", "")).strip()
        if not key:
            raise ValueError(f"Project entry #{index} is missing key.")
        if any(ch.isspace() for ch in key) or "/" in key:
            raise ValueError(f"Project key {key!r} must not contain spaces or '/'.")
        if not name:
            raise ValueError(f"Project entry #{index} is missing name.")
        if key in seen_keys:
            raise ValueError(f"Duplicate project key: {key}.")
        seen_keys.add(key)
        source_channel_ids = [int(value) for value in row.get("source_channel_ids", [])]
        if not source_channel_ids:
            raise ValueError(f"Project {key} must include at least one source channel ID.")
        _validate_positive_unique_ids(source_channel_ids, f"Project {key} source channels")
        post_channel_id = _optional_int_from_json(row.get("post_channel_id"))
        fallback_post_channel_id = _optional_int_from_json(row.get("fallback_post_channel_id"))
        mention_role_id = _optional_int_from_json(row.get("mention_role_id"))
        knowledge_channel_ids = [int(value) for value in row.get("knowledge_channel_ids", [])]
        _validate_positive_unique_ids(knowledge_channel_ids, f"Project {key} knowledge channels")
        knowledge_file_paths = _parse_json_path_list(row.get("knowledge_file_paths"))
        if post_channel_id is not None and post_channel_id <= 0:
            raise ValueError(f"Project {key} post_channel_id must be positive.")
        if fallback_post_channel_id is not None and fallback_post_channel_id <= 0:
            raise ValueError(f"Project {key} fallback_post_channel_id must be positive.")
        if mention_role_id is not None and mention_role_id <= 0:
            raise ValueError(f"Project {key} mention_role_id must be positive.")
        items.append(
            ProjectConfig(
                key=key,
                name=name,
                source_channel_ids=source_channel_ids,
                post_channel_id=post_channel_id,
                fallback_post_channel_id=fallback_post_channel_id,
                mention_role_id=mention_role_id,
                knowledge_channel_ids=knowledge_channel_ids,
                knowledge_file_paths=knowledge_file_paths,
            )
        )
    return items


def _optional_int_from_json(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Project channel IDs must be integers.") from exc


def _parse_ranged_int(raw: str, label: str, minimum: int, maximum: int) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{label} must be an integer.") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}.")
    return value


def _parse_path_list(raw: str) -> list[str]:
    items = [item.strip() for item in raw.replace("\n", ",").split(",") if item.strip()]
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _parse_json_path_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError("knowledge_file_paths must be an array of file paths.")
    items: list[str] = []
    for raw in value:
        cleaned = str(raw).strip()
        if cleaned:
            items.append(cleaned)
    return _parse_path_list(",".join(items))


def _validate_date(raw: str) -> None:
    try:
        datetime.strptime(raw, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Date must use format YYYY-MM-DD.") from exc


def _parse_optional_date_range(raw_from: str, raw_to: str) -> tuple[str | None, str | None]:
    """Parse optional inclusive date range and validate order."""

    date_from = raw_from or None
    date_to = raw_to or None

    if date_from is not None:
        _validate_date(date_from)
    if date_to is not None:
        _validate_date(date_to)
    if date_from and date_to and date_from > date_to:
        raise ValueError("Date range is invalid: date_from must be <= date_to.")

    return date_from, date_to


def _watch_manual_job_process(
    process: subprocess.Popen[Any],
    output_handle: Any,
    output_path: Path,
    mode: str,
    target_date: str | None,
    database_path: str,
) -> None:
    """Wait for dashboard-spawned process and write terminal status logs."""

    return_code = process.wait()
    try:
        output_handle.close()
    except Exception:
        pass

    if return_code == 0:
        log_job_event(
            job_name=mode,
            trigger_source="dashboard",
            status="finished",
            target_date=target_date,
            message=f"Job process finished (exit=0). Output: {output_path}",
            database_path=database_path,
        )
        return

    tail = _tail_file(output_path)
    log_job_event(
        job_name=mode,
        trigger_source="dashboard",
        status="failed",
        target_date=target_date,
        message=(
            f"Job process failed (exit={return_code}). Output: {output_path}. "
            f"Tail: {tail or 'no output'}"
        ),
        database_path=database_path,
    )


def _tail_file(path: Path, max_chars: int = 800) -> str:
    """Return a short tail from a log file for diagnostics."""

    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    compact = " ".join(content.split())
    return compact[-max_chars:]

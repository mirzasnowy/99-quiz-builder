// Employee Quiz Builder — vanilla JS, no framework, no external requests
// except the backend API (window.API_BASE, empty string = no backend).
(function () {
  'use strict';

  var TIME_LIMIT = 25; // seconds per question, all difficulties
  var HEALTH_RETRY_MS = 5000;
  var HEALTH_TOTAL_MS = 60000;
  var GENERATE_TIMEOUT_MS = 120000;
  var JOB_POLL_MS = 2000;      // poll GET /api/jobs/{id} every 2 s
  var JOB_TIMEOUT_MS = 180000; // give up on a job after 3 minutes
  var JOB_POST_TIMEOUT_MS = 15000;
  var TYPICAL_GENERATE_S = 45; // midpoint of the honest "20-60 s" estimate

  var $ = function (id) { return document.getElementById(id); };
  var apiBase = function () {
    return (typeof window.API_BASE === 'string' ? window.API_BASE : '').replace(/\/$/, '');
  };

  // ---------- state ----------
  var quiz = null;          // current Quiz object
  var index = 0;            // current question index
  var score = 0;
  var streak = 0;           // consecutive correct answers
  var answers = [];         // {question, picked, correctLetter, correct, points, secondsLeft, timedOut}
  var timerId = null;
  var deadline = 0;
  var answered = false;
  var generateController = null;
  var jobPollId = null;      // interval id for GET /api/jobs/{id}
  var elapsedTimerId = null; // interval id for the elapsed-time counter
  var jobStart = 0;          // Date.now() when generation began
  var jobMode = null;        // 'job' | 'sync' | null
  var jobSawRepair = false;  // true once the server reports status repairing
  var jobProgress = 0;       // last server progress value (job mode only)
  var samples = [];         // loaded sample quizzes
  var backendLive = false;

  var reducedMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---------- inline SVG icons (thin-line style, per the mockup) ----------
  // No emoji icons anywhere: clock, flame, trophy, check, x, chevron,
  // building, people, gear are all drawn here.
  var ICONS = {
    building: '<path d="M4 21V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v16"/><path d="M2 21h20"/><path d="M9 7h1.5M12.5 7H14M9 11h1.5M12.5 11H14M9 15h1.5M12.5 15H14"/><path d="M16 10h2.5a1.5 1.5 0 0 1 1.5 1.5V21"/>',
    people: '<circle cx="9" cy="8" r="3"/><path d="M3.5 20c.5-3.5 2.8-5.5 5.5-5.5s5 2 5.5 5.5"/><circle cx="17" cy="9" r="2.4"/><path d="M15.5 14.6c2.9.3 5 2.3 5 5.4"/>',
    gear: '<circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="6.5"/><path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M18.7 5.3l-1.8 1.8M7.1 16.9l-1.8 1.8"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
    flame: '<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z"/>',
    trophy: '<path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/><path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22"/><path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22"/><path d="M18 2H6v7a6 6 0 0 0 12 0V2Z"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    chevron: '<path d="m9 18 6-6-6-6"/>'
  };
  function iconSVG(name, size) {
    var s = size || 20;
    return '<svg width="' + s + '" height="' + s + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (ICONS[name] || '') + '</svg>';
  }
  function hydrateIcons(root) {
    var nodes = (root || document).querySelectorAll('[data-icon]');
    nodes.forEach(function (el) {
      if (!el.innerHTML) el.innerHTML = iconSVG(el.getAttribute('data-icon'), 20);
    });
  }

  // Language for generation, set by the segmented control on the home screen.
  var selectedLanguage = 'en';

  // ---------- screens ----------
  var SCREENS = ['screen-home', 'screen-loading', 'screen-quiz', 'screen-results', 'screen-review'];
  function showScreen(id) {
    SCREENS.forEach(function (s) { $(s).hidden = (s !== id); });
    window.scrollTo(0, 0);
  }

  // ---------- backend health chip ----------
  function setChip(text, cls) {
    var chip = $('backend-chip');
    chip.innerHTML = '';
    var dot = document.createElement('span');
    dot.className = 'dot';
    chip.appendChild(dot);
    var label = document.createElement('span');
    label.textContent = text;
    chip.appendChild(label);
    chip.className = 'chip ' + cls;
  }

  function checkHealth() {
    var base = apiBase();
    if (!base) {
      // No backend configured: straight to bundled samples, no retries.
      setChip('Offline: bundled samples only', 'chip-offline');
      backendLive = false;
      return;
    }
    setChip('Waking generator...', 'chip-waking');
    var started = Date.now();
    var tryOnce = function () {
      fetch(base + '/health', { method: 'GET' })
        .then(function (res) {
          if (!res.ok) throw new Error('health ' + res.status);
          return res.json();
        })
        .then(function () {
          backendLive = true;
          setChip('Live generator online', 'chip-live');
        })
        .catch(function () {
          if (Date.now() - started < HEALTH_TOTAL_MS) {
            setTimeout(tryOnce, HEALTH_RETRY_MS);
          } else {
            backendLive = false;
            setChip('Offline: bundled samples only', 'chip-offline');
          }
        });
    };
    tryOnce();
  }

  // ---------- samples ----------
  var FALLBACK_SAMPLES = [
    { file: 'jakarta-property-market.json', title: 'Property Market updates in Jakarta', topic: 'Property Market updates in Jakarta', questions: 10 },
    { file: 'top-skills-property-agent-2025.json', title: 'Top skills of a property agent in 2025', topic: 'Top skills of a property agent in 2025', questions: 10 }
  ];

  function loadSamples() {
    fetch('samples/index.json')
      .then(function (res) { if (!res.ok) throw new Error('index ' + res.status); return res.json(); })
      .then(function (data) { return data.quizzes || FALLBACK_SAMPLES; })
      .catch(function () { return FALLBACK_SAMPLES; })
      .then(function (list) {
        var jobs = list.map(function (entry) {
          return fetch('samples/' + entry.file)
            .then(function (res) { if (!res.ok) throw new Error(entry.file); return res.json(); })
            .catch(function () { return null; });
        });
        return Promise.all(jobs);
      })
      .then(function (loaded) {
        samples = loaded.filter(function (q) { return q && Array.isArray(q.questions); });
        renderSampleCards();
      });
  }

  // Three cards, per the mockup: the two bundled sample quizzes first,
  // then a third card that generates a live quiz on its topic when tapped.
  var GENERATE_CARD = {
    icon: 'people', title: '99 Group Company Culture',
    topic: '99 Group Company Culture'
  };
  var SAMPLE_ICONS = ['building', 'gear'];

  function makeSampleCard(iconName, title, onClick) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'sample-card';
    var icon = document.createElement('span');
    icon.className = 'sample-icon';
    icon.innerHTML = iconSVG(iconName, 28);
    var name = document.createElement('strong');
    name.textContent = title;
    var pill = document.createElement('span');
    pill.className = 'sample-pill';
    pill.textContent = '10 questions';
    var chev = document.createElement('span');
    chev.className = 'chevron';
    chev.innerHTML = iconSVG('chevron', 18);
    btn.appendChild(icon);
    btn.appendChild(name);
    btn.appendChild(pill);
    btn.appendChild(chev);
    btn.addEventListener('click', onClick);
    return btn;
  }

  function renderSampleCards() {
    var wrap = $('sample-cards');
    wrap.innerHTML = '';
    samples.forEach(function (q, i) {
      wrap.appendChild(makeSampleCard(
        SAMPLE_ICONS[i % SAMPLE_ICONS.length],
        q.topic || q.id,
        function () { startQuiz(q); }
      ));
    });
    // Third card: not a bundled sample — it generates a live quiz.
    wrap.appendChild(makeSampleCard(
      GENERATE_CARD.icon,
      GENERATE_CARD.title,
      function () {
        $('topic-input').value = GENERATE_CARD.topic;
        $('generate-form').requestSubmit();
      }
    ));
    if (!samples.length) {
      wrap.textContent = 'Sample quizzes could not be loaded.';
    }
  }

  // ---------- grounding ----------
  function groundingLabel(g) {
    if (g === 'grounded') return 'Grounded';
    if (g === 'partial') return 'Partial';
    return 'Ungrounded';
  }
  function groundingClass(g) {
    if (g === 'grounded') return 'badge-grounded';
    if (g === 'partial') return 'badge-partial';
    return 'badge-ungrounded';
  }

  // ---------- generate: job-based progress screen ----------
  function showGenerateNotice(htmlMessage, withSamples) {
    var box = $('generate-notice');
    box.hidden = false;
    box.innerHTML = '';
    var p = document.createElement('p');
    p.textContent = htmlMessage;
    box.appendChild(p);
    if (withSamples) {
      samples.forEach(function (q) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'btn btn-secondary';
        b.textContent = 'Play sample: ' + (q.topic || q.id);
        b.addEventListener('click', function () { startQuiz(q); });
        box.appendChild(b);
      });
    }
  }

  //
  // Contract (backend):
  //   POST /api/jobs {topic, num_questions, language}
  //     -> 202 {"job_id", "status": "searching", "created_at"}
  //   GET /api/jobs/{job_id}
  //     -> {"job_id", "status", "progress", "stage", "elapsed_s",
  //         "quiz" (when done), "error" (when error)}
  //     Unknown id -> 404.
  // If POST /api/jobs answers 404 the job API is not deployed yet and we
  // fall back to the synchronous POST /api/generate below. The fallback
  // stages are elapsed-time estimates, NOT server state — see the comment
  // in startSyncFlow; the UI says so in #loading-estimate-note too.
  var STAGES = [
    { key: 'searching', label: 'Searching the web' },
    { key: 'drafting', label: 'Drafting questions' },
    { key: 'validating', label: 'Checking answers' },
    { key: 'repairing', label: 'Fixing weak questions' },
    { key: 'shuffling', label: 'Shuffling options' },
    { key: 'done', label: 'Done' }
  ];
  // Server status -> index of the active step in STAGES. 'done' marks the
  // last step; 'shuffling' has no server status of its own (it happens
  // between validating and done), so it is shown as active briefly while
  // the job finishes.
  var STATUS_STEP = { searching: 0, drafting: 1, validating: 2, repairing: 3, done: 5 };

  function resetProgressScreen(topic) {
    jobStart = Date.now();
    jobSawRepair = false;
    jobProgress = 0;
    $('loading-topic-title').textContent = topic;
    $('loading-stage-label').textContent = 'Searching the web…';
    $('loading-progress-fill').style.width = '0%';
    $('loading-estimate-note').hidden = true;
    $('loading-error').hidden = true;
    $('loading-error').innerHTML = '';
    renderStageList(0, false);
    updateTimeLine();
    showScreen('screen-loading');
  }

  // activeIdx: index of the step currently running (STAGES.length = all done).
  // doneJob: true once the job finished, so the repair step can be marked
  // "Not needed" when it never ran.
  function renderStageList(activeIdx, doneJob) {
    var list = $('stage-list');
    list.innerHTML = '';
    STAGES.forEach(function (step, i) {
      var li = document.createElement('li');
      var marker = document.createElement('span');
      marker.className = 'stage-marker';
      var label = document.createElement('span');
      label.textContent = step.label;
      var note = document.createElement('span');
      note.className = 'stage-note';
      if (i === 3 && doneJob && !jobSawRepair) {
        li.className = 'stage-item skipped';
        marker.textContent = '—';
        note.textContent = 'Not needed';
      } else if (i < activeIdx || doneJob) {
        li.className = 'stage-item done';
        marker.textContent = '✓';
      } else if (i === activeIdx) {
        li.className = 'stage-item active';
        marker.textContent = String(i + 1);
      } else {
        li.className = 'stage-item pending';
        marker.textContent = String(i + 1);
      }
      li.appendChild(marker);
      li.appendChild(label);
      li.appendChild(note);
      list.appendChild(li);
    });
  }

  function setLoadingProgress(pct) {
    pct = Math.max(0, Math.min(100, pct));
    $('loading-progress-fill').style.width = pct + '%';
  }

  function elapsedSeconds() {
    return Math.max(0, (Date.now() - jobStart) / 1000);
  }

  function updateTimeLine() {
    var elapsed = Math.floor(elapsedSeconds());
    var text = 'Elapsed ' + elapsed + ' s · usually 20–60 s';
    // The server reports progress only at pipeline boundaries (5, 20, 65,
    // 75, 85, 100), so a linear extrapolation is meaningless while the job
    // is still searching (progress 5). Show a remaining estimate only once
    // drafting has begun, and clamp it so it never looks absurd.
    if (jobMode === 'job' && jobProgress >= 20 && jobProgress < 100) {
      var remaining = Math.round(elapsedSeconds() * (100 - jobProgress) / jobProgress);
      remaining = Math.max(5, Math.min(90, remaining));
      text += ' · about ' + remaining + ' s left';
    }
    $('loading-time').textContent = text;
  }

  function startElapsedClock() {
    stopProgressTimers();
    elapsedTimerId = setInterval(function () {
      updateTimeLine();
      if (jobMode === 'sync') updateSyncEstimate();
      if (jobMode === 'job' && elapsedSeconds() * 1000 > JOB_TIMEOUT_MS) {
        stopProgressTimers();
        showLoadingError('The generator took too long (over 3 minutes). Please try again, or play a sample.');
      }
    }, 250);
  }

  function stopProgressTimers() {
    if (jobPollId !== null) { clearInterval(jobPollId); jobPollId = null; }
    if (elapsedTimerId !== null) { clearInterval(elapsedTimerId); elapsedTimerId = null; }
  }

  function cancelGeneration() {
    stopProgressTimers();
    if (generateController) { generateController.abort(); generateController = null; }
    jobMode = null;
    showScreen('screen-home');
  }

  function showLoadingError(message) {
    stopProgressTimers();
    jobMode = null;
    var box = $('loading-error');
    box.hidden = false;
    box.innerHTML = '';
    var p = document.createElement('p');
    p.textContent = message;
    box.appendChild(p);
    samples.forEach(function (q) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'btn btn-secondary';
      b.textContent = 'Play sample: ' + (q.topic || q.id);
      b.addEventListener('click', function () { startQuiz(q); });
      box.appendChild(b);
    });
    var back = document.createElement('button');
    back.type = 'button';
    back.className = 'btn btn-secondary';
    back.textContent = 'Back home';
    back.addEventListener('click', function () { showScreen('screen-home'); });
    box.appendChild(back);
    $('cancel-generate-btn').hidden = true;
    // Re-show the cancel button next time the progress screen opens.
    setTimeout(function () { $('cancel-generate-btn').hidden = false; }, 0);
  }

  function onGenerateSubmit(event) {
    event.preventDefault();
    var topic = $('topic-input').value.trim();
    if (topic.length < 3 || topic.length > 120) {
      showGenerateNotice('Please enter a topic of 3 to 120 characters.', false);
      return;
    }
    if (!apiBase()) {
      showGenerateNotice('The live generator is not connected, so a new quiz cannot be generated right now. Play a bundled sample instead — they work with no backend.', true);
      return;
    }
    var language = selectedLanguage;
    resetProgressScreen(topic);
    startElapsedClock();
    generateController = new AbortController();
    startJobFlow(topic, language);
  }

  // --- job-based flow (preferred) ---
  function startJobFlow(topic, language) {
    jobMode = 'job';
    var timer = setTimeout(function () { generateController.abort(); }, JOB_POST_TIMEOUT_MS);
    fetch(apiBase() + '/api/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ topic: topic, num_questions: 10, language: language }),
      signal: generateController.signal
    })
      .then(function (res) {
        clearTimeout(timer);
        if (res.status === 404) {
          // Job API not deployed yet: fall back to the synchronous endpoint.
          startSyncFlow(topic, language);
          return null;
        }
        if (!res.ok) throw new Error('The generator answered ' + res.status + '.');
        return res.json();
      })
      .then(function (body) {
        if (!body || !body.job_id) return;
        pollJob(body.job_id);
      })
      .catch(function (err) {
        clearTimeout(timer);
        if (jobMode !== 'job') return; // already fell back or was cancelled
        if (err && err.name === 'AbortError') {
          showLoadingError('The generator did not answer in time. Please try again, or play a sample.');
        } else {
          showLoadingError('Could not start generation: ' + (err.message || 'unknown error'));
        }
      });
  }

  function pollJob(jobId) {
    var tick = function () {
      fetch(apiBase() + '/api/jobs/' + encodeURIComponent(jobId))
        .then(function (res) {
          if (res.status === 404) throw new Error('job-not-found');
          if (!res.ok) throw new Error('The generator answered ' + res.status + '.');
          return res.json();
        })
        .then(function (job) {
          if (jobMode !== 'job') return;
          if (job.status === 'repairing') jobSawRepair = true;
          if (typeof job.progress === 'number') {
            jobProgress = job.progress;
            setLoadingProgress(jobProgress);
          }
          if (job.stage) $('loading-stage-label').textContent = job.stage;
          var step = STATUS_STEP[job.status];
          if (typeof step === 'number') renderStageList(step, job.status === 'done');
          if (job.status === 'done') {
            stopProgressTimers();
            renderStageList(STAGES.length, true);
            setLoadingProgress(100);
            if (job.quiz && Array.isArray(job.quiz.questions)) startQuiz(job.quiz);
            else showLoadingError('The generator finished but returned no quiz. Please try again, or play a sample.');
          } else if (job.status === 'error') {
            var msg = (job.error && job.error.message) || 'The generator failed. Please try again.';
            showLoadingError(msg);
          }
          updateTimeLine();
        })
        .catch(function (err) {
          if (jobMode !== 'job') return;
          stopProgressTimers();
          showLoadingError(err && err.message === 'job-not-found'
            ? 'The generation job was lost on the server. Please try again, or play a sample.'
            : 'Could not check the generation progress: ' + (err.message || 'unknown error'));
        });
    };
    tick();
    jobPollId = setInterval(tick, JOB_POLL_MS);
  }

  // --- synchronous fallback ---
  // FALLBACK (honest): the job API is not deployed yet, so the stages below
  // are elapsed-time estimates, NOT server state. Nothing here knows which
  // phase the server is really in; the UI says so in #loading-estimate-note
  // and the progress bar is capped at 95% until the response arrives.
  function updateSyncEstimate() {
    var elapsed = elapsedSeconds();
    var step = 0;
    var progress = Math.min(95, (elapsed / TYPICAL_GENERATE_S) * 100);
    if (elapsed < 10) { step = 0; $('loading-stage-label').textContent = 'Searching the web…'; }
    else if (elapsed < 35) { step = 1; $('loading-stage-label').textContent = 'Drafting questions…'; }
    else if (elapsed < 50) { step = 2; $('loading-stage-label').textContent = 'Checking answers…'; }
    else { step = 4; $('loading-stage-label').textContent = 'Shuffling options…'; }
    renderStageList(step, false);
    setLoadingProgress(progress);
  }

  function startSyncFlow(topic, language) {
    jobMode = 'sync';
    $('loading-estimate-note').hidden = false;
    $('loading-stage-label').textContent = 'Searching the web…';
    renderStageList(0, false);
    var timer = setTimeout(function () { generateController.abort(); }, GENERATE_TIMEOUT_MS);
    fetch(apiBase() + '/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ topic: topic, num_questions: 10, language: language }),
      signal: generateController.signal
    })
      .then(function (res) { return res.json().then(function (body) { return { res: res, body: body }; }); })
      .then(function (pair) {
        clearTimeout(timer);
        stopProgressTimers();
        if (!pair.res.ok || !pair.body.quiz) {
          var msg = (pair.body.error && pair.body.error.message) || 'The generator failed. Please try again.';
          throw new Error(msg);
        }
        renderStageList(STAGES.length, true);
        setLoadingProgress(100);
        startQuiz(pair.body.quiz);
      })
      .catch(function (err) {
        clearTimeout(timer);
        stopProgressTimers();
        if (jobMode !== 'sync') return;
        var msg = err && err.name === 'AbortError'
          ? 'The generator took too long (over 120 seconds). Please try again, or play a sample.'
          : 'Could not generate a quiz: ' + (err.message || 'unknown error');
        showLoadingError(msg);
      });
  }

  // ---------- quiz play ----------
  function startQuiz(q) {
    quiz = q;
    index = 0;
    score = 0;
    streak = 0;
    answers = [];
    updateStreakBadge(false);
    var badge = $('grounding-badge');
    badge.hidden = false;
    badge.textContent = groundingLabel(q.grounding);
    badge.className = 'badge ' + groundingClass(q.grounding);
    showScreen('screen-quiz');
    renderQuestion(0, false);
  }

  function pointsFor(secondsLeft, newStreak) {
    // 100 + round(50 * secondsLeft / 25) + 10 * min(streak - 1, 5)
    // streak counts consecutive correct answers including this one.
    return 100 + Math.round(50 * secondsLeft / TIME_LIMIT) + 10 * Math.min(newStreak - 1, 5);
  }

  function renderQuestion(i, animate) {
    var q = quiz.questions[i];
    var card = $('question-card');
    answered = false;
    $('question-difficulty').textContent = (q.difficulty || 'medium') + ' · ' + groundingLabel(q.grounded ? 'grounded' : 'ungrounded');
    $('question-text').textContent = q.question;
    $('feedback').textContent = '';
    $('feedback').className = 'feedback';
    $('explanation-box').hidden = true;
    $('next-btn').hidden = true;
    $('next-btn').textContent = (i === quiz.questions.length - 1) ? 'See results' : 'Next';

    var opts = $('options');
    opts.innerHTML = '';
    ['A', 'B', 'C', 'D'].forEach(function (letter) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'option-btn';
      btn.dataset.letter = letter;
      var key = document.createElement('span');
      key.className = 'option-key';
      key.textContent = letter;
      var text = document.createElement('span');
      text.className = 'option-text';
      text.textContent = q.options[letter];
      btn.appendChild(key);
      btn.appendChild(text);
      btn.addEventListener('click', function () { answer(letter); });
      opts.appendChild(btn);
    });

    $('progress-text').textContent = 'Question ' + (i + 1) + ' of ' + quiz.questions.length;
    $('progress-fill').style.width = (((i + 1) / quiz.questions.length) * 100) + '%';
    if (animate && !reducedMotion) {
      card.classList.remove('slide-out');
      card.classList.add('slide-in');
      setTimeout(function () { card.classList.remove('slide-in'); }, 260);
    } else {
      card.classList.remove('slide-out', 'slide-in');
    }
    startTimer();
  }

  function startTimer() {
    stopTimer();
    deadline = Date.now() + TIME_LIMIT * 1000;
    updateTimerDisplay(TIME_LIMIT);
    timerId = setInterval(function () {
      var left = Math.max(0, (deadline - Date.now()) / 1000);
      updateTimerDisplay(left);
      if (left <= 0) {
        stopTimer();
        answer(null, true); // timeout counts as wrong
      }
    }, 100);
  }
  function stopTimer() {
    if (timerId !== null) { clearInterval(timerId); timerId = null; }
  }
  function fmtClock(secondsLeft) {
    var s = Math.max(0, Math.ceil(secondsLeft));
    return '00:' + (s < 10 ? '0' : '') + s;
  }
  function updateTimerDisplay(secondsLeft) {
    $('timer-text').textContent = fmtClock(secondsLeft);
    $('timer-chip').classList.toggle('low', secondsLeft <= 5 && secondsLeft > 0);
  }

  function updateStreakBadge(bump) {
    $('streak-count').textContent = streak;
    var badge = $('streak-badge');
    if (bump && !reducedMotion) {
      badge.classList.remove('bump');
      void badge.offsetWidth; // restart the animation
      badge.classList.add('bump');
    }
  }

  function answer(pickedLetter, timedOut) {
    if (answered) return;
    answered = true;
    stopTimer();
    var q = quiz.questions[index];
    var secondsLeft = timedOut ? 0 : Math.max(0, (deadline - Date.now()) / 1000);
    var correct = pickedLetter === q.correct;
    var points = 0;
    if (correct) {
      streak += 1;
      points = pointsFor(secondsLeft, streak);
      score += points;
    } else {
      streak = 0;
    }
    updateStreakBadge(correct);
    answers.push({
      question: q, picked: pickedLetter, correctLetter: q.correct,
      correct: correct, points: points, secondsLeft: secondsLeft, timedOut: !!timedOut
    });

    // Mark the option buttons: the correct row goes solid green with a
    // checkmark, a wrong pick goes solid red with an x, the rest dim.
    var buttons = $('options').querySelectorAll('.option-btn');
    buttons.forEach(function (btn) {
      btn.disabled = true;
      var letter = btn.dataset.letter;
      var mark = document.createElement('span');
      mark.className = 'option-mark';
      if (letter === q.correct) {
        btn.classList.add('correct');
        mark.innerHTML = iconSVG('check', 20);
        btn.appendChild(mark);
      } else if (letter === pickedLetter) {
        btn.classList.add('wrong');
        mark.innerHTML = iconSVG('x', 20);
        btn.appendChild(mark);
      } else {
        btn.classList.add('dim');
      }
    });

    // Feedback (aria-live) + floating points.
    var fb = $('feedback');
    fb.innerHTML = '';
    if (correct) {
      fb.className = 'feedback correct';
      var msg = document.createElement('span');
      msg.textContent = 'Correct! ';
      var pts = document.createElement('span');
      pts.className = 'points-float';
      pts.textContent = '+' + points;
      fb.appendChild(msg);
      fb.appendChild(pts);
    } else {
      fb.className = 'feedback wrong';
      fb.textContent = timedOut
        ? "Time's up! The correct answer is " + q.correct + '.'
        : 'Not quite — the correct answer is ' + q.correct + '.';
    }

    // Explanation + source link.
    $('explanation-text').textContent = q.explanation || '';
    var src = $('source-line');
    src.innerHTML = '';
    if (q.grounded && q.source_url) {
      var label = document.createElement('span');
      label.textContent = 'Source: ';
      var link = document.createElement('a');
      link.href = q.source_url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = q.source_title || q.source_url;
      src.appendChild(label);
      src.appendChild(link);
      if (q.evidence) {
        var ev = document.createElement('span');
        ev.textContent = ' — “' + q.evidence + '”';
        src.appendChild(ev);
      }
    } else {
      var flag = document.createElement('span');
      flag.className = 'flag';
      flag.textContent = 'Ungrounded — no source for this question.';
      src.appendChild(flag);
    }
    $('explanation-box').hidden = false;
    $('next-btn').hidden = false;
    $('next-btn').focus();
  }

  function onNext() {
    var card = $('question-card');
    var advance = function () {
      if (index + 1 < quiz.questions.length) {
        index += 1;
        renderQuestion(index, true);
      } else {
        showResults();
      }
    };
    if (reducedMotion) { advance(); return; }
    card.classList.add('slide-out');
    setTimeout(advance, 250);
  }

  // ---------- results ----------
  function gradeFor(correctCount, total) {
    var pct = total ? (correctCount / total) * 100 : 0;
    if (pct >= 90) return { tier: 'Gold', title: 'Property Pro' };
    if (pct >= 70) return { tier: 'Silver', title: 'Market Savvy' };
    if (pct >= 50) return { tier: 'Bronze', title: 'Getting There' };
    return { tier: '', title: 'Keep Learning' };
  }

  function bestKey() { return 'quiz-best-' + (quiz ? quiz.id : 'unknown'); }
  function loadBest() {
    try {
      var raw = window.localStorage.getItem(bestKey());
      var n = raw === null ? null : parseInt(raw, 10);
      return isNaN(n) ? null : n;
    } catch (e) { return null; }
  }
  function saveBest(value) {
    try { window.localStorage.setItem(bestKey(), String(value)); } catch (e) { /* storage unavailable */ }
  }

  function messageFor(grade) {
    if (grade.tier === 'Gold') return "Great job! You're a true 99 Group expert.";
    if (grade.tier === 'Silver') return 'Nice work — you know the market well.';
    if (grade.tier === 'Bronze') return 'Good start — keep going, the next round is yours.';
    return 'Keep learning — try again and watch the score climb.';
  }

  function showResults() {
    stopTimer();
    showScreen('screen-results');
    var total = quiz.questions.length;
    var correctCount = answers.filter(function (a) { return a.correct; }).length;
    var pct = Math.round((correctCount / total) * 100);
    var grade = gradeFor(correctCount, total);

    // Big "8 / 10": the correct count is huge, the total is small.
    $('score-total').textContent = ' / ' + total;

    // Gold pill with a trophy icon (silver / bronze / none for the rest).
    var pill = $('grade-pill');
    pill.innerHTML = '';
    var trophy = document.createElement('span');
    trophy.className = 'icon';
    trophy.innerHTML = iconSVG('trophy', 20);
    var pillText = document.createElement('span');
    pillText.textContent = (grade.tier ? grade.tier + ' — ' : '') + grade.title;
    pill.appendChild(trophy);
    pill.appendChild(pillText);
    pill.className = 'grade-pill' + (grade.tier ? ' grade-' + grade.tier.toLowerCase() : ' grade-none');

    $('grade-message').textContent = messageFor(grade);
    $('correct-line').textContent = score + ' points earned';

    var prevBest = loadBest();
    var isNewBest = prevBest === null || score > prevBest;
    if (isNewBest) saveBest(score);
    var best = loadBest();
    $('best-line').textContent = best === null
      ? 'Personal best: not saved (browser storage unavailable).'
      : 'Personal best for this quiz: ' + best + ' points' + (isNewBest && prevBest !== null ? ' — new best!' : '');

    // Count-up on the big correct count: 1.2 s, ease-out, requestAnimationFrame.
    var el = $('score-count');
    if (reducedMotion) { el.textContent = correctCount; }
    else {
      var start = performance.now();
      var tick = function (now) {
        var t = Math.min(1, (now - start) / 1200);
        var eased = 1 - Math.pow(1 - t, 3);
        el.textContent = Math.round(correctCount * eased);
        if (t < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    }

    if (pct >= 80 && typeof window.launchConfetti === 'function') {
      window.launchConfetti();
    }
    $('progress-fill').style.width = '100%';
  }

  // ---------- review ----------
  function showReview() {
    showScreen('screen-review');
    var total = quiz.questions.length;
    var correctCount = answers.filter(function (a) { return a.correct; }).length;
    $('review-summary').textContent = correctCount + ' of ' + total + ' correct · ' + score + ' points';
    var list = $('review-list');
    list.innerHTML = '';
    answers.forEach(function (a, i) {
      var item = document.createElement('div');
      item.className = 'review-item';
      var h = document.createElement('h3');
      h.textContent = (i + 1) + '. ' + a.question.question;
      item.appendChild(h);

      var yours = document.createElement('p');
      yours.innerHTML = '';
      var yLabel = document.createElement('strong');
      yLabel.textContent = 'Your answer: ';
      yours.appendChild(yLabel);
      var yVal = document.createElement('span');
      yVal.className = a.correct ? 'review-correct' : 'review-wrong';
      yVal.textContent = a.timedOut
        ? 'No answer (time ran out)'
        : a.picked + '. ' + a.question.options[a.picked];
      yours.appendChild(yVal);
      if (a.correct) {
        var pts = document.createElement('span');
        pts.textContent = ' (+' + a.points + ' points)';
        yours.appendChild(pts);
      }
      item.appendChild(yours);

      if (!a.correct) {
        var right = document.createElement('p');
        var rLabel = document.createElement('strong');
        rLabel.textContent = 'Correct answer: ';
        var rVal = document.createElement('span');
        rVal.className = 'review-correct';
        rVal.textContent = a.correctLetter + '. ' + a.question.options[a.correctLetter];
        right.appendChild(rLabel);
        right.appendChild(rVal);
        item.appendChild(right);
      }

      var ex = document.createElement('p');
      ex.textContent = a.question.explanation || '';
      item.appendChild(ex);

      var src = document.createElement('p');
      src.className = 'source-line';
      if (a.question.grounded && a.question.source_url) {
        var sLabel = document.createElement('span');
        sLabel.textContent = 'Source: ';
        var link = document.createElement('a');
        link.href = a.question.source_url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = a.question.source_title || a.question.source_url;
        src.appendChild(sLabel);
        src.appendChild(link);
      } else {
        var flag = document.createElement('span');
        flag.className = 'flag';
        flag.textContent = 'Ungrounded — no source for this question.';
        src.appendChild(flag);
      }
      var badge = document.createElement('span');
      badge.className = 'badge ' + groundingClass(a.question.grounded ? 'grounded' : 'ungrounded');
      badge.textContent = a.question.grounded ? 'Grounded' : 'Ungrounded';
      src.appendChild(badge);
      item.appendChild(src);

      list.appendChild(item);
    });
  }

  // ---------- wire up ----------
  document.addEventListener('DOMContentLoaded', function () {
    hydrateIcons(document);
    checkHealth();
    loadSamples();
    document.querySelectorAll('.segmented-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        selectedLanguage = btn.dataset.lang;
        document.querySelectorAll('.segmented-btn').forEach(function (b) {
          var active = b === btn;
          b.classList.toggle('is-active', active);
          b.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
      });
    });
    $('generate-form').addEventListener('submit', onGenerateSubmit);
    $('cancel-generate-btn').addEventListener('click', cancelGeneration);
    $('next-btn').addEventListener('click', onNext);
    $('review-btn').addEventListener('click', showReview);
    $('review-back-btn').addEventListener('click', function () { showScreen('screen-results'); });
    $('replay-btn').addEventListener('click', function () { if (quiz) startQuiz(quiz); });
    $('home-btn').addEventListener('click', function () {
      stopTimer();
      $('generate-notice').hidden = true;
      showScreen('screen-home');
    });
  });
})();

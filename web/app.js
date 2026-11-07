// Employee Quiz Builder — vanilla JS, no framework, no external requests
// except the backend API (window.API_BASE, empty string = no backend).
(function () {
  'use strict';

  var TIME_LIMIT = 25; // seconds per question, all difficulties
  var HEALTH_RETRY_MS = 5000;
  var HEALTH_TOTAL_MS = 60000;
  var GENERATE_TIMEOUT_MS = 120000;

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
  var samples = [];         // loaded sample quizzes
  var backendLive = false;

  var reducedMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---------- screens ----------
  var SCREENS = ['screen-home', 'screen-loading', 'screen-quiz', 'screen-results', 'screen-review'];
  function showScreen(id) {
    SCREENS.forEach(function (s) { $(s).hidden = (s !== id); });
    window.scrollTo(0, 0);
  }

  // ---------- backend health chip ----------
  function setChip(text, cls) {
    var chip = $('backend-chip');
    chip.textContent = text;
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

  function renderSampleCards() {
    var wrap = $('sample-cards');
    wrap.innerHTML = '';
    samples.forEach(function (q) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'sample-card';
      var title = document.createElement('strong');
      title.textContent = q.topic || q.id;
      var meta = document.createElement('span');
      meta.textContent = q.questions.length + ' questions · ' + groundingLabel(q.grounding);
      btn.appendChild(title);
      btn.appendChild(meta);
      btn.addEventListener('click', function () { startQuiz(q); });
      wrap.appendChild(btn);
    });
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

  // ---------- generate ----------
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
    var language = $('language-select').value || 'en';
    $('loading-topic').textContent = 'Topic: ' + topic;
    showScreen('screen-loading');
    generateController = new AbortController();
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
        if (!pair.res.ok || !pair.body.quiz) {
          var msg = (pair.body.error && pair.body.error.message) || 'The generator failed. Please try again.';
          throw new Error(msg);
        }
        startQuiz(pair.body.quiz);
      })
      .catch(function (err) {
        clearTimeout(timer);
        showScreen('screen-home');
        var msg = err && err.name === 'AbortError'
          ? 'The generator took too long (over 120 seconds). Please try again, or play a sample.'
          : 'Could not generate a quiz: ' + (err.message || 'unknown error') + ' You can still play a bundled sample.';
        showGenerateNotice(msg, true);
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
      key.textContent = letter + '.';
      var text = document.createElement('span');
      text.textContent = q.options[letter];
      btn.appendChild(key);
      btn.appendChild(text);
      btn.addEventListener('click', function () { answer(letter); });
      opts.appendChild(btn);
    });

    $('progress-text').textContent = 'Question ' + (i + 1) + ' / ' + quiz.questions.length;
    $('progress-fill').style.width = ((i / quiz.questions.length) * 100) + '%';
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
  function updateTimerDisplay(secondsLeft) {
    var fill = $('timer-fill');
    fill.style.width = Math.max(0, (secondsLeft / TIME_LIMIT) * 100) + '%';
    fill.classList.toggle('low', secondsLeft <= 5 && secondsLeft > 0);
    $('timer-text').textContent = Math.ceil(secondsLeft) + ' s';
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

    // Mark the option buttons.
    var buttons = $('options').querySelectorAll('.option-btn');
    buttons.forEach(function (btn) {
      btn.disabled = true;
      var letter = btn.dataset.letter;
      if (letter === q.correct) btn.classList.add('correct');
      else if (letter === pickedLetter) btn.classList.add('wrong');
      else btn.classList.add('dim');
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

  function showResults() {
    stopTimer();
    showScreen('screen-results');
    var total = quiz.questions.length;
    var correctCount = answers.filter(function (a) { return a.correct; }).length;
    var pct = Math.round((correctCount / total) * 100);
    var grade = gradeFor(correctCount, total);

    $('grade-line').textContent = (grade.tier ? grade.tier + ' — ' : '') + grade.title;
    $('correct-line').textContent = correctCount + ' of ' + total + ' correct (' + pct + '%)';

    var prevBest = loadBest();
    var isNewBest = prevBest === null || score > prevBest;
    if (isNewBest) saveBest(score);
    var best = loadBest();
    $('best-line').textContent = best === null
      ? 'Personal best: not saved (browser storage unavailable).'
      : 'Personal best for this quiz: ' + best + ' points' + (isNewBest && prevBest !== null ? ' — new best!' : '');

    // Score count-up: 1.2 s, ease-out, requestAnimationFrame.
    var el = $('score-count');
    if (reducedMotion) { el.textContent = score; }
    else {
      var start = performance.now();
      var tick = function (now) {
        var t = Math.min(1, (now - start) / 1200);
        var eased = 1 - Math.pow(1 - t, 3);
        el.textContent = Math.round(score * eased);
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
    checkHealth();
    loadSamples();
    $('generate-form').addEventListener('submit', onGenerateSubmit);
    $('cancel-generate-btn').addEventListener('click', function () {
      if (generateController) generateController.abort();
      showScreen('screen-home');
    });
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

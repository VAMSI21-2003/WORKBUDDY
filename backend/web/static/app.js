// WorkBuddy page script (frontend design 3.3). It has no user-visible text of its own: every string
// it shows comes from a <template data-copy> element in the page, or from the API's own error
// message, and it writes text only with textContent. Its jobs, and nothing else:
//   1. put an error response's message in the slot of the control that made the request;
//   2. poll retries: wait Retry-After on a 429, back off on a network error or 5xx;
//   3. the swap guard: drop a fragment for another session, or older than what the page holds;
//   4. a lost answer to a plan: keep the buttons disabled and ask the server what happened;
//   5. an ambiguous message POST: ask the server whether it made the turn;
//   6. reload after the password gate; 7. Enter sends and Shift+Enter adds a line.
(() => {
  'use strict';

  // Seconds to wait before each retry of a poll that couldn't reach the server. After the last
  // one, the next problem ends the polling (about 30 s in all).
  const RETRY_DELAYS_S = [2, 4, 8, 8, 8];
  const DEFAULT_RETRY_AFTER_S = 5;
  const GONE_CODES = ['session_not_found', 'turn_not_found', 'file_not_found'];

  const find = (selector, root = document) => root.querySelector(selector);
  const copyText = (key) => {
    const holder = find('template[data-copy="' + key + '"]');
    return holder ? holder.content.textContent.trim() : '';
  };
  const reload = () => window.location.reload();
  const pageSessionId = () => {
    const workspace = find('#workspace');
    return workspace ? workspace.dataset.sessionId || '' : '';
  };

  function parseError(xhr) {
    try {
      const body = JSON.parse(xhr.responseText);
      if (body && body.error && typeof body.error.message === 'string') {
        return { code: String(body.error.code), message: body.error.message };
      }
    } catch (ignored) {
      // not the API's body: a proxy's page, for example
    }
    return null;
  }

  function retryAfterSeconds(xhr) {
    const seconds = parseInt(xhr.getResponseHeader('Retry-After'), 10);
    return Number.isFinite(seconds) && seconds >= 1 ? seconds : DEFAULT_RETRY_AFTER_S;
  }

  // -- Where a message goes ------------------------------------------------------------------
  // Page notices have their own region, never the reply slot (design M5).
  function showNotice(text, key, withLink) {
    const area = find('#page-notices');
    if (!area) return;
    let notice = find('[data-notice="' + key + '"]', area);
    if (!notice) {
      notice = document.createElement('p');
      notice.className = 'notice';
      notice.setAttribute('role', 'alert');
      notice.dataset.notice = key;
      area.appendChild(notice);
    }
    notice.textContent = text;
    if (withLink) {
      const link = document.createElement('a');
      link.href = '/';
      link.textContent = copyText('enter-password-again');
      notice.append(' ', link);
    }
  }

  function slotOf(elt) {
    const holder = elt.closest('[data-error-slot]');
    if (!holder || holder.dataset.errorSlot === 'page-notices') return null;
    return document.getElementById(holder.dataset.errorSlot);
  }

  function showInSlot(elt, text, key) {
    const slot = slotOf(elt);
    if (slot) {
      slot.textContent = text;
    } else {
      showNotice(text, key || 'request');
    }
  }

  // Disable a control for a while, once htmx has put its own disabled state back.
  function disableFor(buttons, seconds) {
    window.setTimeout(() => {
      buttons.forEach((button) => { button.disabled = true; });
      window.setTimeout(() => {
        buttons.forEach((button) => { if (button.isConnected) button.disabled = false; });
      }, seconds * 1000);
    }, 0);
  }

  const buttonsIn = (holder) => Array.from(holder.querySelectorAll('button'));

  // -- Polls -----------------------------------------------------------------------------------
  function retryPoll(poll) {
    if (poll.isConnected) htmx.trigger(poll, 'wb:retry');
  }

  function stopPolling() {
    document.querySelectorAll('.poll').forEach((poll) => poll.remove());
  }

  function pollProblem(poll, xhr) {
    const status = xhr ? xhr.status : 0;
    if (status === 401) {
      stopPolling();
      const error = xhr && parseError(xhr);
      if (error) showNotice(error.message, 'auth', true);
      return;
    }
    if (status === 404) {
      reload();
      return;
    }
    if (status === 429) {
      // A refused request, not a lost connection: it doesn't count (design H2).
      window.setTimeout(() => retryPoll(poll), retryAfterSeconds(xhr) * 1000);
      return;
    }
    const failures = Number(poll.dataset.failures || '0') + 1;
    poll.dataset.failures = String(failures);
    if (failures > RETRY_DELAYS_S.length) {
      poll.remove();
      showNotice(copyText('lost-contact'), 'poll');
      return;
    }
    window.setTimeout(() => retryPoll(poll), RETRY_DELAYS_S[failures - 1] * 1000);
  }

  // -- A lost answer to a plan -----------------------------------------------------------------
  function lostAnswer(form) {
    // htmx puts the buttons back when the request ends; they stay off until the server says what
    // state the plan is in.
    window.setTimeout(() => { buttonsIn(form).forEach((button) => { button.disabled = true; }); }, 0);
    const turn = form.closest('.turn');
    const id = form.querySelector('[name="confirmation_id"]').value;
    const url = form.dataset.refetchUrl + '?unconfirmed=' + encodeURIComponent(id);
    htmx.ajax('GET', url, { target: turn }).catch(() => {
      showNotice(copyText('cant-reach'), 'request');
    });
  }

  // -- An ambiguous message POST ---------------------------------------------------------------
  const lines = (text) => text.replace(/\r\n/g, '\n').trim();

  function ambiguousPost(form) {
    const box = form.querySelector('textarea');
    const draft = box.value;
    const lastSeq = Number(form.querySelector('[name="last_seq"]').value) || 0;
    const send = form.querySelector('button[type="submit"]');
    window.setTimeout(() => { send.disabled = true; }, 0);
    fetch(form.dataset.transcriptUrl + '?after=' + lastSeq, { credentials: 'same-origin' })
      .then((response) => {
        if (!response.ok) throw new Error('transcript');
        return response.text();
      })
      .then((text) => {
        const page = new DOMParser().parseFromString(text, 'text/html');
        const newer = Array.from(page.querySelectorAll('li.turn')).filter(
          (item) => Number(item.dataset.seq) > lastSeq,
        );
        const mine = newer.some((item) => {
          const message = item.querySelector('[data-slot="message"]');
          return message && lines(message.textContent) === lines(draft);
        });
        if (!mine) showNotice(copyText('not-confirmed'), 'ambiguous');
        if (newer.length === 0) {
          send.disabled = false;
          return null;
        }
        // The server has a newer turn: show the session as it is. A draft that isn't that turn's
        // message stays in the box.
        return htmx.ajax('GET', form.dataset.transcriptUrl, { target: '#conversation' }).then(() => {
          if (!mine) {
            const fresh = document.getElementById('message');
            if (fresh) fresh.value = draft;
          }
        });
      })
      .catch(() => {
        // Send stays off until the session has been fetched (a reload does that).
        showNotice(copyText('cant-reach'), 'request');
      });
  }

  function refetchConversation(form) {
    const box = form.querySelector('textarea');
    const draft = box.value;
    htmx.ajax('GET', form.dataset.transcriptUrl, { target: '#conversation' })
      .then(() => {
        const fresh = document.getElementById('message');
        if (fresh) fresh.value = draft;
      })
      .catch(() => showNotice(copyText('cant-reach'), 'request'));
  }

  // -- Errors ----------------------------------------------------------------------------------
  function onResponseError(evt) {
    const elt = evt.detail.elt;
    const xhr = evt.detail.xhr;
    if (elt.classList.contains('poll')) {
      pollProblem(elt, xhr);
      return;
    }
    const error = parseError(xhr);
    const status = xhr.status;
    const gate = elt.closest('[data-gate]');
    const composer = elt.closest('[data-composer]');
    const plan = elt.closest('[data-plan-form]');
    if (!error) {
      // Not the API's body (a proxy's page): nothing to quote.
      showNotice(copyText('cant-reach'), 'request');
      if (composer) ambiguousPost(composer);
      if (plan) lostAnswer(plan);
      return;
    }
    if (gate) {
      showInSlot(gate, error.message);
      if (status === 401) {
        const field = find('#password');
        field.value = '';
        field.focus();
      }
      if (status === 429) disableFor(buttonsIn(gate), retryAfterSeconds(xhr));
      return;
    }
    if (status === 401) {
      stopPolling();
      showNotice(error.message, 'auth', true);
      return;
    }
    if (status === 404 && GONE_CODES.includes(error.code)) {
      reload();
      return;
    }
    if (status === 503 && error.code === 'storage_full') {
      showNotice(error.message, 'storage');
      const upload = find('#upload-form');
      if (upload) disableFor(buttonsIn(upload), 3600);
      return;
    }
    showInSlot(elt, error.message);
    if (status === 429) {
      const holder = elt.closest('[data-error-slot]');
      if (holder) disableFor(buttonsIn(holder), retryAfterSeconds(xhr));
    } else if (status === 409 && error.code === 'session_busy' && composer) {
      refetchConversation(composer);
    } else if (status === 409 && plan) {
      const poll = plan.closest('.turn').querySelector('.poll');
      if (poll) retryPoll(poll);
    } else if (status >= 500 && composer) {
      ambiguousPost(composer);
    } else if (status >= 500 && plan) {
      lostAnswer(plan);
    }
  }

  function onSendError(evt) {
    const elt = evt.detail.elt;
    if (elt.classList.contains('poll')) {
      pollProblem(elt, null);
      return;
    }
    const composer = elt.closest('[data-composer]');
    const plan = elt.closest('[data-plan-form]');
    if (composer) {
      ambiguousPost(composer);
    } else if (plan) {
      lostAnswer(plan);
    } else {
      showNotice(copyText('cant-reach'), 'request');
    }
  }

  // -- The swap guard (design H1, M4) ----------------------------------------------------------
  function guardSwap(evt) {
    const text = evt.detail.serverResponse;
    if (typeof text !== 'string' || text === '') return;
    const fragment = new DOMParser().parseFromString(text, 'text/html').querySelector('[data-session-id]');
    if (!fragment) return;
    let drop = false;
    const page = pageSessionId();
    if (page !== '' && fragment.dataset.sessionId !== page) drop = true;
    const turnId = fragment.dataset.turnId;
    const version = Number(fragment.dataset.version);
    if (!drop && turnId && Number.isFinite(version)) {
      const held = document.getElementById('turn-' + turnId);
      if (held && Number(held.dataset.version) > version) drop = true;
    }
    if (!drop) return;
    evt.detail.shouldSwap = false;
    const poll = evt.detail.elt;
    if (poll && poll.classList && poll.classList.contains('poll')) {
      window.setTimeout(() => retryPoll(poll), RETRY_DELAYS_S[0] * 1000);
    }
  }

  document.addEventListener('htmx:beforeRequest', (evt) => {
    const slot = slotOf(evt.detail.elt);
    if (slot) slot.textContent = '';
  });
  document.addEventListener('htmx:responseError', onResponseError);
  document.addEventListener('htmx:sendError', onSendError);
  document.addEventListener('htmx:timeout', onSendError);
  document.addEventListener('htmx:beforeSwap', guardSwap);
  document.addEventListener('htmx:afterRequest', (evt) => {
    if (evt.detail.successful && evt.detail.elt.matches && evt.detail.elt.matches('[data-gate]')) {
      reload();
    }
  });

  // Enter sends, Shift+Enter adds a line.
  document.addEventListener('keydown', (evt) => {
    const box = evt.target;
    if (evt.key !== 'Enter' || evt.shiftKey || evt.isComposing) return;
    if (!(box instanceof HTMLTextAreaElement) || box.id !== 'message') return;
    evt.preventDefault();
    if (box.form && box.value.trim() !== '') box.form.requestSubmit();
  });

  // After a swap that brings in an element with autofocus, make sure it has the focus: htmx
  // honours autofocus for the swapped target but not always for an out-of-band element.
  document.addEventListener('htmx:afterSettle', (evt) => {
    const target = evt.detail.elt;
    if (!target || !target.querySelector) return;
    const wanted = target.matches('[autofocus]') ? target : target.querySelector('[autofocus]');
    if (wanted && document.activeElement !== wanted) wanted.focus();
  });
})();

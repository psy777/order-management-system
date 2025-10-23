(function () {
    if (typeof window === 'undefined') {
        return;
    }
    if (window.FireCoastReminderNotifier) {
        return;
    }

    const MAX_TIMEOUT = 2147483647; // ~24 days

    const defaultTimeZone = (() => {
        try {
            return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
        } catch (error) {
            return 'UTC';
        }
    })();

    const state = {
        timers: new Map(),
        notified: new Set(),
        pendingFetch: null,
    };

    const getReminderKey = (reminder) => {
        if (!reminder) {
            return '';
        }
        return String(reminder.id || reminder.handle || '');
    };

    const markNotified = (reminder) => {
        const key = getReminderKey(reminder);
        if (!key) {
            return true;
        }
        if (state.notified.has(key)) {
            return false;
        }
        state.notified.add(key);
        return true;
    };

    const resetNotified = (reminderOrKey) => {
        const key = typeof reminderOrKey === 'string' ? reminderOrKey : getReminderKey(reminderOrKey);
        if (!key) {
            return;
        }
        state.notified.delete(key);
    };

    const formatTimerLabel = (seconds) => {
        if (!Number.isFinite(seconds) || seconds <= 0) {
            return '';
        }
        let remaining = Math.floor(seconds);
        const parts = [];
        const units = [
            { value: 86400, suffix: 'd' },
            { value: 3600, suffix: 'h' },
            { value: 60, suffix: 'm' },
            { value: 1, suffix: 's' },
        ];
        units.forEach(({ value, suffix }) => {
            if (remaining >= value) {
                const amount = Math.floor(remaining / value);
                remaining -= amount * value;
                parts.push(`${amount}${suffix}`);
            }
        });
        return parts.join('');
    };

    const formatDueDescription = (reminder) => {
        if (!reminder || !reminder.due_at) {
            return '';
        }
        try {
            const tz = reminder.timezone || defaultTimeZone;
            const date = new Date(reminder.due_at);
            if (Number.isNaN(date.getTime())) {
                return '';
            }
            const dateFormatter = new Intl.DateTimeFormat('en-US', {
                timeZone: tz,
                month: 'short',
                day: 'numeric',
                year: 'numeric',
            });
            const datePart = dateFormatter.format(date);
            if (!reminder.due_has_time) {
                return `Due ${datePart}`;
            }
            const timeFormatter = new Intl.DateTimeFormat('en-US', {
                timeZone: tz,
                hour: 'numeric',
                minute: '2-digit',
            });
            const timePart = timeFormatter.format(date);
            return `Due ${datePart} at ${timePart}`;
        } catch (error) {
            return '';
        }
    };

    const describeTimerCountdown = (reminder) => {
        if (!reminder) {
            return '';
        }
        const timerSeconds = Number(reminder.timer_seconds || reminder.timerSeconds || 0);
        if (!Number.isFinite(timerSeconds) || timerSeconds <= 0) {
            return '';
        }
        const target = reminder.remind_at || reminder.due_at;
        if (!target) {
            return '';
        }
        const targetDate = new Date(target);
        if (Number.isNaN(targetDate.getTime())) {
            return '';
        }
        const deltaSeconds = Math.max(0, Math.round((targetDate.getTime() - Date.now()) / 1000));
        if (deltaSeconds <= 0) {
            return `Timer ${formatTimerLabel(timerSeconds)} finished.`;
        }
        return `Timer ${formatTimerLabel(timerSeconds)} due in ${formatTimerLabel(deltaSeconds)}.`;
    };

    const showNotification = (reminder) => {
        if (!reminder) {
            return;
        }
        if (!markNotified(reminder)) {
            return;
        }
        const title = reminder.title || 'Reminder';
        const timerSummary = describeTimerCountdown(reminder);
        const dueSummary = formatDueDescription(reminder);
        const body = timerSummary || dueSummary || 'Reminder is due now.';
        if ('Notification' in window && Notification.permission === 'granted') {
            try {
                new Notification(title, { body });
            } catch (error) {
                console.debug('Reminder notification error', error);
            }
        }
        window.dispatchEvent(
            new CustomEvent('firecoast:reminder-notified', {
                detail: { reminder },
            }),
        );
    };

    const clearInactiveTimers = (activeKeys) => {
        state.timers.forEach((timeoutId, key) => {
            if (!activeKeys.has(key)) {
                window.clearTimeout(timeoutId);
                state.timers.delete(key);
                state.notified.delete(key);
            }
        });
    };

    const scheduleTimers = (reminders) => {
        const activeKeys = new Set();
        reminders.forEach((reminder) => {
            const key = getReminderKey(reminder);
            if (!key) {
                return;
            }
            activeKeys.add(key);
            if (reminder.completed) {
                if (state.timers.has(key)) {
                    window.clearTimeout(state.timers.get(key));
                    state.timers.delete(key);
                }
                state.notified.delete(key);
                return;
            }
            const target = reminder.remind_at || reminder.due_at;
            if (!target) {
                if (state.timers.has(key)) {
                    window.clearTimeout(state.timers.get(key));
                    state.timers.delete(key);
                }
                state.notified.delete(key);
                return;
            }
            const targetDate = new Date(target);
            if (Number.isNaN(targetDate.getTime())) {
                return;
            }
            const delay = targetDate.getTime() - Date.now();
            if (delay <= 0) {
                showNotification(reminder);
                if (state.timers.has(key)) {
                    window.clearTimeout(state.timers.get(key));
                    state.timers.delete(key);
                }
                return;
            }
            if (delay > MAX_TIMEOUT) {
                state.notified.delete(key);
                if (state.timers.has(key)) {
                    window.clearTimeout(state.timers.get(key));
                    state.timers.delete(key);
                }
                return;
            }
            if (state.timers.has(key)) {
                window.clearTimeout(state.timers.get(key));
                state.timers.delete(key);
            }
            state.notified.delete(key);
            const timeoutId = window.setTimeout(() => {
                showNotification(reminder);
                state.timers.delete(key);
            }, delay);
            state.timers.set(key, timeoutId);
        });
        clearInactiveTimers(activeKeys);
    };

    const fetchReminders = async () => {
        if (state.pendingFetch) {
            return state.pendingFetch;
        }
        const params = new URLSearchParams();
        params.set('status', 'active');
        params.set('kind', 'reminder');
        const url = `/api/reminders?${params.toString()}`;
        const promise = fetch(url)
            .then((response) => {
                if (!response.ok) {
                    throw new Error('Failed to fetch reminders');
                }
                return response.json();
            })
            .then((payload) => {
                const reminders = Array.isArray(payload.reminders) ? payload.reminders : [];
                scheduleTimers(reminders);
                return reminders;
            })
            .catch((error) => {
                console.debug('Reminder notifier fetch failed', error);
            })
            .finally(() => {
                state.pendingFetch = null;
            });
        state.pendingFetch = promise;
        return promise;
    };

    const requestPermissionIfNeeded = () => {
        if (!('Notification' in window)) {
            return;
        }
        if (Notification.permission === 'default') {
            try {
                Notification.requestPermission().catch(() => {});
            } catch (error) {
                // ignore
            }
        }
    };

    const refreshOnDemand = () => {
        fetchReminders();
    };

    window.FireCoastReminderNotifier = {
        refresh: refreshOnDemand,
        registerNotification: markNotified,
        resetReminder: resetNotified,
        ingestReminders: scheduleTimers,
    };

    requestPermissionIfNeeded();
    fetchReminders();

    const POLL_INTERVAL = 60000;
    window.setInterval(fetchReminders, POLL_INTERVAL);
    window.addEventListener('focus', refreshOnDemand);
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) {
            refreshOnDemand();
        }
    });
    window.addEventListener('firecoast:reminders-changed', refreshOnDemand);
})();

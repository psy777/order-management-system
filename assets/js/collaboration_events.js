(function () {
    if (typeof window === 'undefined') {
        return;
    }
    if (!('EventSource' in window)) {
        console.warn('Server-sent events are not supported in this browser.');
        return;
    }
    if (window.fireCoastCollaboration && window.fireCoastCollaboration.eventSource) {
        return;
    }

    const EVENT_NAME = 'firecoast:collaboration-event';
    const STATUS_EVENT = 'firecoast:collaboration-status';

    const dispatch = (type, detail) => {
        try {
            window.dispatchEvent(new CustomEvent(type, { detail }));
        } catch (error) {
            console.error('Failed to dispatch collaboration event', error);
        }
    };

    const eventSource = new EventSource('/api/events');

    const broadcastStatus = (status) => {
        dispatch(STATUS_EVENT, { status });
    };

    eventSource.onopen = () => {
        broadcastStatus('connected');
    };

    eventSource.onerror = () => {
        broadcastStatus('disconnected');
    };

    eventSource.onmessage = (event) => {
        if (!event || !event.data) {
            return;
        }
        let payload;
        try {
            payload = JSON.parse(event.data);
        } catch (error) {
            console.warn('Unable to parse collaboration event', error);
            return;
        }
        if (!payload || typeof payload !== 'object') {
            return;
        }
        dispatch(EVENT_NAME, payload);
    };

    window.addEventListener('beforeunload', () => {
        try {
            eventSource.close();
        } catch (error) {
            console.debug('Failed to close collaboration event source', error);
        }
    });

    window.fireCoastCollaboration = {
        eventSource,
        eventName: EVENT_NAME,
        statusEvent: STATUS_EVENT,
    };
})();

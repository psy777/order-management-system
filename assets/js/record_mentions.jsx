const { useEffect, useMemo, useRef, useState } = React;

const MENTION_REGEX = /(^|\s)@([a-z0-9_.-]+)/gi;

const fetchHandles = async (entityTypes = [], search = '') => {
    const params = new URLSearchParams();
    if (entityTypes && entityTypes.length > 0) {
        params.set('entity_types', entityTypes.join(','));
    }
    if (search) {
        params.set('q', search);
    }
    const response = await fetch(`/api/records/handles?${params.toString()}`);
    if (!response.ok) {
        throw new Error('Failed to load mention handles');
    }
    const payload = await response.json();
    return payload.handles || [];
};

function useMentionDirectory(entityTypes = ['contact']) {
    const [handles, setHandles] = useState([]);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState(null);

    const normalisedTypes = useMemo(() => entityTypes.map(type => type.toLowerCase()), [entityTypes]);

    const refresh = async () => {
        setIsLoading(true);
        setError(null);
        try {
            const data = await fetchHandles(normalisedTypes);
            setHandles(data);
        } catch (err) {
            console.error('Failed to load mention handles', err);
            setError(err);
        } finally {
            setIsLoading(false);
        }
    };

    useEffect(() => {
        refresh();
    }, [normalisedTypes.join(',')]);

    return { handles, refresh, isLoading, error };
}

function RecordMentionTextarea({
    label,
    placeholder,
    value,
    onChange,
    disabled = false,
    rows = 3,
    entityTypes = ['contact'],
}) {
    const textareaRef = useRef(null);
    const overlayRef = useRef(null);
    const [suggestions, setSuggestions] = useState([]);
    const [isOpen, setIsOpen] = useState(false);
    const [highlightIndex, setHighlightIndex] = useState(0);
    const lastCaretRef = useRef(0);
    const { handles, refresh } = useMentionDirectory(entityTypes);

    const escapeHtml = (input = '') => String(input)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');

    const formatSpaces = (input = '') => input.replace(/ {2,}/g, match => '&nbsp;'.repeat(match.length - 1) + ' ');

    const buildHighlightedHtml = useMemo(() => {
        const rawValue = value || '';
        if (!rawValue.trim()) {
            return `<span class="text-slate-400">${escapeHtml(placeholder || '')}</span>`;
        }
        const escaped = escapeHtml(rawValue);
        const withMentions = escaped.replace(
            MENTION_REGEX,
            (match, prefix, handle) => `${prefix}<span class="mention-pill inline-flex items-center gap-1 rounded-full bg-orange-100 px-2 py-0.5 text-xs font-semibold text-orange-700">@${handle}</span>`
        );
        const withBreaks = withMentions.replace(/\r?\n/g, '<br />');
        return formatSpaces(withBreaks);
    }, [value, placeholder]);

    const syncOverlayScroll = () => {
        const textarea = textareaRef.current;
        const overlay = overlayRef.current;
        if (!textarea || !overlay) return;
        overlay.scrollTop = textarea.scrollTop;
        overlay.scrollLeft = textarea.scrollLeft;
    };

    const closeSuggestions = () => {
        setSuggestions([]);
        setIsOpen(false);
        setHighlightIndex(0);
    };

    useEffect(() => {
        const textarea = textareaRef.current;
        if (!textarea) return;
        const handleScroll = () => syncOverlayScroll();
        textarea.addEventListener('scroll', handleScroll);
        return () => textarea.removeEventListener('scroll', handleScroll);
    }, []);

    const computeSuggestions = (inputValue, caret) => {
        const text = inputValue.slice(0, caret);
        const mentionStart = text.lastIndexOf('@');
        if (mentionStart === -1) {
            closeSuggestions();
            return;
        }
        const prefix = text.slice(mentionStart + 1);
        if (prefix.includes(' ') || prefix.includes('\n')) {
            closeSuggestions();
            return;
        }
        lastCaretRef.current = caret;
        const term = prefix.toLowerCase();
        const filtered = handles.filter(entry => {
            if (!entry || !entry.handle) return false;
            return (
                entry.handle.toLowerCase().startsWith(term) ||
                (entry.displayName && entry.displayName.toLowerCase().includes(term))
            );
        });
        setSuggestions(filtered.slice(0, 8));
        setIsOpen(filtered.length > 0);
        setHighlightIndex(0);
    };

    const replaceWithSuggestion = suggestion => {
        const textarea = textareaRef.current;
        if (!textarea) return;
        const caret = lastCaretRef.current;
        const text = textarea.value;
        const mentionStart = text.slice(0, caret).lastIndexOf('@');
        if (mentionStart === -1) return;
        const mentionEnd = caret;
        const replacement = `@${suggestion.handle}`;
        const newValue = text.slice(0, mentionStart) + replacement + text.slice(mentionEnd);
        onChange(newValue);
        requestAnimationFrame(() => {
            const newCaret = mentionStart + replacement.length;
            textarea.setSelectionRange(newCaret, newCaret);
            textarea.focus();
        });
        closeSuggestions();
    };

    const handleKeyDown = event => {
        if (!isOpen) return;
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            setHighlightIndex(prev => (prev + 1) % suggestions.length);
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            setHighlightIndex(prev => (prev - 1 + suggestions.length) % suggestions.length);
        } else if (event.key === 'Enter' || event.key === 'Tab') {
            event.preventDefault();
            const suggestion = suggestions[highlightIndex];
            if (suggestion) {
                replaceWithSuggestion(suggestion);
            }
        } else if (event.key === 'Escape') {
            event.preventDefault();
            closeSuggestions();
        }
    };

    const handleInput = event => {
        const text = event.target.value;
        onChange(text);
        computeSuggestions(text, event.target.selectionStart);
    };

    const handleBlur = () => {
        setTimeout(() => closeSuggestions(), 150);
    };

    return (
        <div className="space-y-2">
            {label && <label className="block text-sm font-medium text-slate-600">{label}</label>}
            <div className="relative">
                <div
                    ref={overlayRef}
                    className={`absolute inset-0 pointer-events-none whitespace-pre-wrap break-words rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-transparent selection:bg-orange-200 selection:text-inherit ${disabled ? 'bg-slate-100' : ''}`}
                    dangerouslySetInnerHTML={{ __html: buildHighlightedHtml }}
                />
                <textarea
                    ref={textareaRef}
                    value={value || ''}
                    onChange={handleInput}
                    onKeyDown={handleKeyDown}
                    onBlur={handleBlur}
                    onScroll={syncOverlayScroll}
                    placeholder={placeholder}
                    disabled={disabled}
                    rows={rows}
                    className="relative z-10 block w-full resize-none rounded-md border border-slate-300 bg-transparent px-3 py-2 text-sm text-slate-700 focus:border-orange-500 focus:outline-none focus:ring-2 focus:ring-orange-200 disabled:cursor-not-allowed disabled:bg-slate-100"
                />
                {isOpen && suggestions.length > 0 && (
                    <ul className="absolute z-20 mt-1 max-h-48 w-full overflow-auto rounded-md border border-slate-200 bg-white shadow-lg">
                        {suggestions.map((suggestion, index) => (
                            <li
                                key={`${suggestion.entityType}:${suggestion.entityId}`}
                                className={`cursor-pointer px-3 py-2 text-sm ${highlightIndex === index ? 'bg-orange-50 text-orange-700' : 'text-slate-700'}`}
                                onMouseDown={event => {
                                    event.preventDefault();
                                    replaceWithSuggestion(suggestion);
                                }}
                            >
                                <div className="font-semibold">@{suggestion.handle}</div>
                                {suggestion.displayName && (
                                    <div className="text-xs text-slate-500">
                                        {suggestion.displayName}
                                        {suggestion.entityType ? ` · ${suggestion.entityType}` : ''}
                                    </div>
                                )}
                            </li>
                        ))}
                        <li className="border-t border-slate-200 px-3 py-2 text-xs text-slate-400">
                            <button
                                type="button"
                                className="text-orange-600 hover:text-orange-700"
                                onMouseDown={event => {
                                    event.preventDefault();
                                    refresh();
                                }}
                            >
                                Refresh directory
                            </button>
                        </li>
                    </ul>
                )}
            </div>
        </div>
    );
}

window.RecordMentionComponents = {
    RecordMentionTextarea,
    useMentionDirectory,
};

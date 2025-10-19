const { useEffect, useMemo, useRef, useState } = React;

const MENTION_REGEX = /(^|\s)@([a-z0-9_.-]+)/gi;
const ZERO_WIDTH_SPACE = String.fromCharCode(8203);

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
    const containerRef = useRef(null);
    const textareaRef = useRef(null);
    const overlayRef = useRef(null);
    const menuRef = useRef(null);
    const [suggestions, setSuggestions] = useState([]);
    const [isOpen, setIsOpen] = useState(false);
    const [highlightIndex, setHighlightIndex] = useState(0);
    const lastCaretRef = useRef(0);
    const { handles, refresh, isLoading, error } = useMentionDirectory(entityTypes);
    const [contextMenu, setContextMenu] = useState({
        isOpen: false,
        handle: null,
        position: { top: 0, left: 0 },
        metadata: null,
    });
    const [menuFeedback, setMenuFeedback] = useState('');

    const handlesMap = useMemo(() => {
        const map = new Map();
        (handles || []).forEach(entry => {
            if (!entry || !entry.handle) {
                return;
            }
            map.set(entry.handle.toLowerCase(), entry);
        });
        return map;
    }, [handles]);

    const activeContextHandle = contextMenu.handle;

    const getEntityLabel = metadata => {
        if (!metadata || !metadata.entityType) {
            return '';
        }
        return metadata.entityType.replace(/_/g, ' ');
    };

    const getRecordUrl = metadata => {
        if (!metadata) {
            return null;
        }
        const entityType = (metadata.entityType || '').toLowerCase();
        const entityId = metadata.entityId;
        if (!entityId) {
            return null;
        }
        if (entityType === 'contact') {
            return `/contacts?contact_id=${encodeURIComponent(entityId)}`;
        }
        if (entityType === 'order') {
            return `/orders?order_id=${encodeURIComponent(entityId)}`;
        }
        return `/api/records/${entityType}/${encodeURIComponent(entityId)}`;
    };

    const getActivityUrl = metadata => {
        if (!metadata) {
            return null;
        }
        const entityType = (metadata.entityType || '').toLowerCase();
        const entityId = metadata.entityId;
        if (!entityId) {
            return null;
        }
        return `/api/records/${entityType}/${encodeURIComponent(entityId)}/activity`;
    };

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

    const closeContextMenu = () => {
        setContextMenu({
            isOpen: false,
            handle: null,
            position: { top: 0, left: 0 },
            metadata: null,
        });
        setMenuFeedback('');
    };

    useEffect(() => {
        const textarea = textareaRef.current;
        if (!textarea) return;
        const handleScroll = () => syncOverlayScroll();
        textarea.addEventListener('scroll', handleScroll);
        return () => textarea.removeEventListener('scroll', handleScroll);
    }, []);

    useEffect(() => {
        if (!contextMenu.isOpen) {
            return undefined;
        }
        const handleGlobalClick = event => {
            if (menuRef.current && menuRef.current.contains(event.target)) {
                return;
            }
            if (containerRef.current && containerRef.current.contains(event.target)) {
                const pillTarget = event.target.closest('[data-mention-handle]');
                if (pillTarget) {
                    return;
                }
            }
            closeContextMenu();
        };
        const handleGlobalScroll = () => closeContextMenu();
        const handleGlobalKey = event => {
            if (event.key === 'Escape') {
                event.preventDefault();
                closeContextMenu();
            }
        };
        window.addEventListener('mousedown', handleGlobalClick);
        window.addEventListener('scroll', handleGlobalScroll, true);
        window.addEventListener('resize', handleGlobalScroll);
        window.addEventListener('keydown', handleGlobalKey);
        return () => {
            window.removeEventListener('mousedown', handleGlobalClick);
            window.removeEventListener('scroll', handleGlobalScroll, true);
            window.removeEventListener('resize', handleGlobalScroll);
            window.removeEventListener('keydown', handleGlobalKey);
        };
    }, [contextMenu.isOpen]);

    useEffect(() => {
        if (!contextMenu.isOpen || !activeContextHandle) {
            return;
        }
        const updatedMetadata = handlesMap.get(activeContextHandle);
        if (!updatedMetadata) {
            return;
        }
        setContextMenu(prev => {
            if (!prev.isOpen || prev.handle !== activeContextHandle) {
                return prev;
            }
            if (prev.metadata === updatedMetadata) {
                return prev;
            }
            return {
                ...prev,
                metadata: updatedMetadata,
            };
        });
    }, [contextMenu.isOpen, activeContextHandle, handlesMap]);

    useEffect(() => {
        if (suggestions.length === 0) {
            setHighlightIndex(0);
        } else if (highlightIndex >= suggestions.length) {
            setHighlightIndex(0);
        }
    }, [suggestions, highlightIndex]);

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
        const filtered = (handles || []).filter(entry => {
            if (!entry || !entry.handle) return false;
            return (
                entry.handle.toLowerCase().startsWith(term) ||
                (entry.displayName && entry.displayName.toLowerCase().includes(term))
            );
        });
        setSuggestions(filtered.slice(0, 8));
        setIsOpen(true);
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
        closeContextMenu();
    };

    const handleKeyDown = event => {
        if (event.key === 'Escape') {
            if (contextMenu.isOpen) {
                event.preventDefault();
                closeContextMenu();
                return;
            }
            if (isOpen) {
                event.preventDefault();
                closeSuggestions();
            }
            return;
        }
        if (!isOpen || suggestions.length === 0) {
            return;
        }
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            setHighlightIndex(prev => (prev + 1) % suggestions.length);
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            setHighlightIndex(prev => (prev - 1 + suggestions.length) % suggestions.length);
        } else if (event.key === 'Enter' || event.key === 'Tab') {
            event.preventDefault();
            const suggestion = suggestions[highlightIndex] || suggestions[0];
            if (suggestion) {
                replaceWithSuggestion(suggestion);
            }
        }
    };

    const handleInput = event => {
        const text = event.target.value;
        onChange(text);
        closeContextMenu();
        computeSuggestions(text, event.target.selectionStart);
    };

    const handleBlur = () => {
        setTimeout(() => closeSuggestions(), 150);
    };

    const copyHandleToClipboard = async handleValue => {
        const handleText = `@${handleValue}`;
        try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(handleText);
            } else {
                const input = document.createElement('textarea');
                input.value = handleText;
                input.setAttribute('readonly', '');
                input.style.position = 'absolute';
                input.style.left = '-9999px';
                document.body.appendChild(input);
                input.select();
                document.execCommand('copy');
                document.body.removeChild(input);
            }
            setMenuFeedback('Handle copied to clipboard');
            setTimeout(() => setMenuFeedback(''), 2000);
        } catch (err) {
            console.error('Failed to copy handle', err);
            setMenuFeedback('Unable to copy automatically');
            setTimeout(() => setMenuFeedback(''), 2500);
        }
    };

    const highlightNodes = useMemo(() => {
        const rawValue = value || '';
        if (!rawValue.trim()) {
            if (placeholder) {
                return <span className="text-sm text-slate-400">{placeholder}</span>;
            }
            return <span className="text-transparent">{ZERO_WIDTH_SPACE}</span>;
        }

        const nodes = [];
        MENTION_REGEX.lastIndex = 0;
        let lastIndex = 0;
        let match;
        while ((match = MENTION_REGEX.exec(rawValue)) !== null) {
            const prefixLength = match[1] ? match[1].length : 0;
            const mentionStart = match.index + prefixLength;
            if (mentionStart > lastIndex) {
                const textSegment = rawValue.slice(lastIndex, mentionStart);
                nodes.push(
                    <span key={`text-${lastIndex}-${mentionStart}`} className="text-transparent">
                    {textSegment || ZERO_WIDTH_SPACE}
                    </span>
                );
            }
            const handle = (match[2] || '').toLowerCase();
            const metadata = handlesMap.get(handle);
            const badgeLabel = getEntityLabel(metadata);
            const mentionLength = handle.length + 1;
            nodes.push(
                <button
                    key={`mention-${mentionStart}-${handle}`}
                    type="button"
                    data-mention-handle={handle}
                    tabIndex={-1}
                    style={{ pointerEvents: 'auto' }}
                    className="mention-pill pointer-events-auto inline-flex items-center gap-1 rounded-full border border-orange-200 bg-orange-50 px-2 py-0.5 text-xs font-semibold text-orange-700 shadow-sm transition-colors hover:bg-orange-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-orange-500"
                    onMouseDown={event => {
                        event.preventDefault();
                        event.stopPropagation();
                        const target = event.currentTarget;
                        const rect = target.getBoundingClientRect();
                        const estimatedHeight = 220;
                        const estimatedWidth = 280;
                        let top = rect.bottom + 8;
                        if (top + estimatedHeight > window.innerHeight - 12) {
                            top = Math.max(rect.top - estimatedHeight - 8, 12);
                        }
                        let left = rect.left;
                        if (left + estimatedWidth > window.innerWidth - 12) {
                            left = Math.max(window.innerWidth - estimatedWidth - 12, 12);
                        }
                        left = Math.max(left, 12);
                        setContextMenu({
                            isOpen: true,
                            handle,
                            position: { top: Math.round(top), left: Math.round(left) },
                            metadata: metadata || null,
                        });
                        setMenuFeedback('');
                        requestAnimationFrame(() => {
                            textareaRef.current?.focus();
                        });
                    }}
                    title={metadata?.displayName ? `${metadata.displayName} (${metadata.entityType || 'record'})` : `@${handle}`}
                >
                    @{handle}
                    {badgeLabel && (
                        <span className="rounded-full bg-orange-100 px-1 text-[10px] font-semibold uppercase tracking-wide text-orange-600">
                            {badgeLabel}
                        </span>
                    )}
                </button>
            );
            lastIndex = mentionStart + mentionLength;
        }

        if (lastIndex < rawValue.length) {
            const trailing = rawValue.slice(lastIndex);
            nodes.push(
                <span key={`text-${lastIndex}-${rawValue.length}`} className="text-transparent">
                    {trailing || ZERO_WIDTH_SPACE}
                </span>
            );
        }

        if (nodes.length === 0) {
            return <span className="text-transparent">{rawValue || ZERO_WIDTH_SPACE}</span>;
        }

        return nodes;
    }, [value, placeholder, handlesMap]);

    const isActive = (isOpen && !disabled) || contextMenu.isOpen;
    const overlayStateClasses = disabled
        ? 'border-slate-200 bg-slate-100'
        : 'border-slate-300 bg-white';
    const overlayFocusClasses = !disabled && isActive ? 'border-orange-300 shadow-sm ring-2 ring-orange-200' : '';

    const contextMetadata = contextMenu.metadata;
    const contextEntityLabel = getEntityLabel(contextMetadata);
    const recordUrl = getRecordUrl(contextMetadata);
    const activityUrl = getActivityUrl(contextMetadata);
    const hasUiDestination = recordUrl && !recordUrl.startsWith('/api/records/');
    const apiUrl = contextMetadata && contextMetadata.entityId && hasUiDestination
        ? `/api/records/${(contextMetadata.entityType || '').toLowerCase()}/${encodeURIComponent(contextMetadata.entityId)}`
        : null;

    return (
        <div className="space-y-2" ref={containerRef}>
            {label && <label className="block text-sm font-medium text-slate-600">{label}</label>}
            <div className="relative">
                <div
                    ref={overlayRef}
                    className={`absolute inset-0 whitespace-pre-wrap break-words rounded-md px-3 py-2 text-sm text-transparent transition duration-150 ${overlayStateClasses} ${overlayFocusClasses} pointer-events-none selection:bg-orange-200 selection:text-inherit`}
                >
                    {highlightNodes}
                </div>
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
                    className="relative z-10 block w-full resize-none rounded-md border border-transparent bg-transparent px-3 py-2 text-sm text-slate-700 focus:border-transparent focus:outline-none focus:ring-0 disabled:cursor-not-allowed disabled:text-slate-500"
                />
                {isOpen && (
                    <div className="absolute bottom-full left-0 right-0 z-30 mb-2">
                        <ul className="max-h-60 overflow-auto rounded-xl border border-slate-200 bg-white shadow-2xl ring-1 ring-slate-100" role="listbox">
                            <li className="border-b border-slate-100 bg-slate-50 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                                Mention a record
                            </li>
                            {suggestions.map((suggestion, index) => {
                                const isHighlighted = index === highlightIndex;
                                const entityLabel = suggestion.entityType ? suggestion.entityType.replace(/_/g, ' ') : '';
                                return (
                                    <li
                                        key={`${suggestion.entityType || 'record'}:${suggestion.entityId}`}
                                        role="option"
                                        aria-selected={isHighlighted}
                                        className={`cursor-pointer px-4 py-3 text-sm transition-colors ${isHighlighted ? 'bg-orange-50 text-orange-700' : 'text-slate-700 hover:bg-slate-50'}`}
                                        onMouseDown={event => {
                                            event.preventDefault();
                                            replaceWithSuggestion(suggestion);
                                        }}
                                        onMouseEnter={() => setHighlightIndex(index)}
                                    >
                                        <div className="flex items-center justify-between gap-2">
                                            <span className="font-semibold">@{suggestion.handle}</span>
                                            {entityLabel && (
                                                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                                                    {entityLabel}
                                                </span>
                                            )}
                                        </div>
                                        {suggestion.displayName && (
                                            <div className="mt-1 text-xs text-slate-500">{suggestion.displayName}</div>
                                        )}
                                    </li>
                                );
                            })}
                            {suggestions.length === 0 && !isLoading && !error && (
                                <li className="px-4 py-3 text-sm text-slate-500">
                                    No matching records yet. Try typing more or refresh the directory.
                                </li>
                            )}
                            {error && (
                                <li className="px-4 py-3 text-sm text-red-600">Unable to load mention directory. Please refresh.</li>
                            )}
                            {isLoading && (
                                <li className="px-4 py-3 text-sm text-slate-500">Loading directory…</li>
                            )}
                            <li className="border-t border-slate-100 bg-white px-4 py-2">
                                <button
                                    type="button"
                                    className="text-sm font-semibold text-orange-600 transition hover:text-orange-700"
                                    onMouseDown={event => {
                                        event.preventDefault();
                                        refresh();
                                    }}
                                >
                                    Refresh directory
                                </button>
                            </li>
                        </ul>
                    </div>
                )}
                {contextMenu.isOpen && contextMenu.handle && (
                    <div
                        ref={menuRef}
                        className="fixed z-50 w-64 rounded-xl border border-slate-200 bg-white p-4 shadow-2xl ring-1 ring-slate-100"
                        style={{ top: contextMenu.position.top, left: contextMenu.position.left }}
                    >
                        <div className="space-y-1">
                            <div className="text-sm font-semibold text-slate-900">@{contextMenu.handle}</div>
                            {contextMetadata?.displayName && (
                                <div className="text-xs text-slate-500">{contextMetadata.displayName}</div>
                            )}
                            {contextEntityLabel && (
                                <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-600">
                                    {contextEntityLabel}
                                </span>
                            )}
                        </div>
                        <div className="mt-3 flex flex-col gap-1 text-sm">
                            <button
                                type="button"
                                className="flex items-center justify-between rounded-md px-2 py-2 text-left transition hover:bg-orange-50 hover:text-orange-700"
                                onClick={() => {
                                    if (!recordUrl) {
                                        setMenuFeedback('No record destination yet.');
                                        return;
                                    }
                                    window.open(recordUrl, '_blank');
                                    closeContextMenu();
                                }}
                                disabled={!recordUrl}
                            >
                                <span>{hasUiDestination ? 'Open record' : 'View record JSON'}</span>
                                {!recordUrl && <span className="text-xs text-slate-400">Unavailable</span>}
                            </button>
                            <button
                                type="button"
                                className="flex items-center justify-between rounded-md px-2 py-2 text-left transition hover:bg-orange-50 hover:text-orange-700"
                                onClick={() => {
                                    if (!activityUrl) {
                                        setMenuFeedback('No activity log available yet.');
                                        return;
                                    }
                                    window.open(activityUrl, '_blank');
                                    closeContextMenu();
                                }}
                                disabled={!activityUrl}
                            >
                                <span>Open activity log</span>
                                {!activityUrl && <span className="text-xs text-slate-400">Unavailable</span>}
                            </button>
                            <button
                                type="button"
                                className="flex items-center justify-between rounded-md px-2 py-2 text-left transition hover:bg-orange-50 hover:text-orange-700"
                                onClick={() => copyHandleToClipboard(contextMenu.handle)}
                            >
                                <span>Copy handle</span>
                                <span className="text-xs text-slate-400">@{contextMenu.handle}</span>
                            </button>
                            {apiUrl && (
                                <button
                                    type="button"
                                    className="flex items-center justify-between rounded-md px-2 py-2 text-left transition hover:bg-orange-50 hover:text-orange-700"
                                    onClick={() => {
                                        window.open(apiUrl, '_blank');
                                        closeContextMenu();
                                    }}
                                >
                                    <span>Open raw record</span>
                                    <span className="text-xs text-slate-400">API</span>
                                </button>
                            )}
                            <button
                                type="button"
                                className="flex items-center justify-between rounded-md px-2 py-2 text-left transition hover:bg-orange-50 hover:text-orange-700"
                                onClick={() => {
                                    refresh();
                                    setMenuFeedback('Directory refresh requested');
                                    setTimeout(() => setMenuFeedback(''), 2000);
                                }}
                            >
                                <span>Refresh directory</span>
                            </button>
                        </div>
                        {menuFeedback && (
                            <div className="mt-3 text-xs font-medium text-emerald-600">{menuFeedback}</div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}

window.RecordMentionComponents = {
    RecordMentionTextarea,
    useMentionDirectory,
};

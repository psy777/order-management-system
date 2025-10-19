const { useCallback, useEffect, useMemo, useRef, useState } = React;

const MENTION_REGEX = /(^|[^a-z0-9_.-])@([a-z0-9_.-]+)/gi;
const ZERO_WIDTH_SPACE = String.fromCharCode(8203);

const MENU_DIMENSIONS = { width: 280, height: 220 };

const fetchHandles = async (entityTypes = null, search = '') => {
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

    const normalisedTypes = useMemo(() => {
        if (!entityTypes) {
            return null;
        }
        const list = Array.isArray(entityTypes) ? entityTypes : [entityTypes];
        const filtered = list.filter(Boolean).map(type => type.toLowerCase());
        return filtered.length > 0 ? filtered : null;
    }, [entityTypes]);

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
    }, [normalisedTypes ? normalisedTypes.join(',') : 'all']);

    return { handles, refresh, isLoading, error };
}

const normaliseHandle = handle => (handle || '').toLowerCase();

const buildHandlesMap = handles => {
    const map = new Map();
    (handles || []).forEach(entry => {
        if (!entry || !entry.handle) {
            return;
        }
        map.set(normaliseHandle(entry.handle), entry);
    });
    return map;
};

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
        return `/order/${encodeURIComponent(entityId)}`;
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

const computeMenuPosition = rect => {
    const estimatedWidth = MENU_DIMENSIONS.width;
    const estimatedHeight = MENU_DIMENSIONS.height;
    let top = rect.bottom + 8;
    if (top + estimatedHeight > window.innerHeight - 12) {
        top = Math.max(rect.top - estimatedHeight - 8, 12);
    }
    let left = rect.left;
    if (left + estimatedWidth > window.innerWidth - 12) {
        left = Math.max(window.innerWidth - estimatedWidth - 12, 12);
    }
    left = Math.max(left, 12);
    return { top: Math.round(top), left: Math.round(left) };
};

const getDisplayLabel = (metadata, handle) => {
    if (metadata && metadata.displayName) {
        const trimmed = metadata.displayName.trim();
        if (trimmed) {
            return trimmed;
        }
    }
    if (metadata && metadata.label) {
        const trimmed = metadata.label.trim();
        if (trimmed) {
            return trimmed;
        }
    }
    return handle;
};

const renderMentionContent = (text, handlesMap, { renderMention, renderText, placeholder }) => {
    const rawValue = text || '';
    if (!rawValue.trim()) {
        return placeholder;
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
            nodes.push(renderText(textSegment, lastIndex, mentionStart));
        }
        const handle = normaliseHandle(match[2]);
        const metadata = handlesMap.get(handle) || null;
        const mentionLength = match[2].length + 1;
        nodes.push(renderMention({
            handle,
            metadata,
            displayLabel: getDisplayLabel(metadata, match[2]),
            start: mentionStart,
            end: mentionStart + mentionLength,
        }));
        lastIndex = mentionStart + mentionLength;
    }

    if (lastIndex < rawValue.length) {
        const trailing = rawValue.slice(lastIndex);
        nodes.push(renderText(trailing, lastIndex, rawValue.length));
    }

    return nodes.length > 0 ? nodes : placeholder;
};

function useMentionContextMenu({ handlesMap, refresh, containerRef }) {
    const menuRef = useRef(null);
    const [contextMenu, setContextMenu] = useState({
        isOpen: false,
        handle: null,
        rawHandle: null,
        position: { top: 0, left: 0 },
        metadata: null,
    });
    const [menuFeedback, setMenuFeedback] = useState('');

    const closeContextMenu = useCallback(() => {
        setContextMenu({
            isOpen: false,
            handle: null,
            rawHandle: null,
            position: { top: 0, left: 0 },
            metadata: null,
        });
        setMenuFeedback('');
    }, []);

    useEffect(() => {
        if (!contextMenu.isOpen || !contextMenu.handle) {
            return;
        }
        const updatedMetadata = handlesMap.get(contextMenu.handle);
        if (!updatedMetadata) {
            return;
        }
        setContextMenu(prev => {
            if (!prev.isOpen || prev.handle !== contextMenu.handle) {
                return prev;
            }
            if (prev.metadata === updatedMetadata) {
                return prev;
            }
            return { ...prev, metadata: updatedMetadata };
        });
    }, [contextMenu.handle, contextMenu.isOpen, handlesMap]);

    useEffect(() => {
        if (!contextMenu.isOpen) {
            return undefined;
        }
        const handleGlobalClick = event => {
            if (menuRef.current && menuRef.current.contains(event.target)) {
                return;
            }
            if (containerRef && containerRef.current && containerRef.current.contains(event.target)) {
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
    }, [closeContextMenu, containerRef, contextMenu.isOpen]);

    const copyHandleToClipboard = useCallback(async handleValue => {
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
    }, []);

    const openContextMenuAtRect = useCallback((handle, metadata, rect, options = {}) => {
        const normalisedHandle = normaliseHandle(handle);
        const resolvedMetadata = metadata || handlesMap.get(normalisedHandle) || null;
        const position = computeMenuPosition(rect);
        setContextMenu({
            isOpen: true,
            handle: normalisedHandle,
            rawHandle: handle,
            position,
            metadata: resolvedMetadata,
        });
        setMenuFeedback('');
        const { onAfterOpen } = options || {};
        requestAnimationFrame(() => {
            if (typeof onAfterOpen === 'function') {
                onAfterOpen();
            }
        });
    }, [handlesMap]);

    const openContextMenuFromEvent = useCallback((event, handle, metadata, options = {}) => {
        event.preventDefault();
        event.stopPropagation();
        const rect = event.currentTarget.getBoundingClientRect();
        openContextMenuAtRect(handle, metadata, rect, options);
    }, [openContextMenuAtRect]);

    const contextMetadata = contextMenu.metadata;
    const contextEntityLabel = getEntityLabel(contextMetadata);
    const recordUrl = getRecordUrl(contextMetadata);
    const activityUrl = getActivityUrl(contextMetadata);
    const hasUiDestination = recordUrl && !recordUrl.startsWith('/api/records/');
    const apiUrl = contextMetadata && contextMetadata.entityId && hasUiDestination
        ? `/api/records/${(contextMetadata.entityType || '').toLowerCase()}/${encodeURIComponent(contextMetadata.entityId)}`
        : null;
    const resolvedHandleForDisplay = contextMenu.rawHandle || contextMenu.handle || '';

    const ContextMenu = contextMenu.isOpen ? (
        <div
            ref={menuRef}
            className="fixed z-50 w-64 rounded-xl border border-slate-200 bg-white p-4 shadow-2xl ring-1 ring-slate-100"
            style={{ top: contextMenu.position.top, left: contextMenu.position.left }}
        >
            <div className="space-y-1">
                <div className="text-sm font-semibold text-slate-900">{getDisplayLabel(contextMetadata, resolvedHandleForDisplay)}</div>
                <div className="text-xs text-slate-500">@{resolvedHandleForDisplay}</div>
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
                    onClick={() => copyHandleToClipboard(resolvedHandleForDisplay)}
                >
                    <span>Copy handle</span>
                    <span className="text-xs text-slate-400">@{resolvedHandleForDisplay}</span>
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
                        if (typeof refresh === 'function') {
                            refresh();
                        }
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
    ) : null;

    return {
        contextMenu,
        isOpen: contextMenu.isOpen,
        openContextMenuFromEvent,
        closeContextMenu,
        ContextMenu,
        setMenuFeedback,
    };
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
    const [suggestions, setSuggestions] = useState([]);
    const [isOpen, setIsOpen] = useState(false);
    const [highlightIndex, setHighlightIndex] = useState(0);
    const lastCaretRef = useRef(0);
    const { handles, refresh, isLoading, error } = useMentionDirectory(entityTypes);
    const handlesMap = useMemo(() => buildHandlesMap(handles), [handles]);
    const {
        ContextMenu,
        isOpen: isContextMenuOpen,
        openContextMenuFromEvent,
        closeContextMenu,
    } = useMentionContextMenu({ handlesMap, refresh, containerRef });

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
            if (isContextMenuOpen) {
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

    const highlightNodes = useMemo(() => renderMentionContent(value, handlesMap, {
        renderText: (segment, start, end) => (
            <span
                key={`text-${start}-${end}`}
                className="text-sm text-slate-700"
                style={{ pointerEvents: 'none' }}
            >
                {segment || ZERO_WIDTH_SPACE}
            </span>
        ),
        renderMention: ({ handle, metadata, displayLabel, start }) => {
            const badgeLabel = getEntityLabel(metadata);
            return (
                <button
                    key={`mention-${start}-${handle}`}
                    type="button"
                    data-mention-handle={handle}
                    tabIndex={-1}
                    style={{ pointerEvents: 'auto' }}
                    className="mention-pill pointer-events-auto inline-flex max-w-full items-center gap-1 rounded-full border border-orange-200 bg-orange-50 px-2 py-0.5 text-xs font-semibold text-orange-700 shadow-sm transition-colors hover:bg-orange-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-orange-500"
                    onMouseDown={event => {
                        openContextMenuFromEvent(event, handle, metadata, {
                            onAfterOpen: () => {
                                textareaRef.current?.focus();
                            },
                        });
                    }}
                    aria-label={metadata?.displayName ? `Mention: ${metadata.displayName}` : `Mention handle ${handle}`}
                    title={metadata?.displayName ? `${metadata.displayName} (@${handle})` : `@${handle}`}
                >
                    <span className="truncate">{displayLabel}</span>
                    {badgeLabel && (
                        <span className="rounded-full bg-orange-100 px-1 text-[10px] font-semibold uppercase tracking-wide text-orange-600">
                            {badgeLabel}
                        </span>
                    )}
                </button>
            );
        },
        placeholder: placeholder
            ? (
                <span className="text-sm text-slate-400" style={{ pointerEvents: 'none' }}>
                    {placeholder}
                </span>
            )
            : (
                <span className="text-sm text-slate-700" style={{ pointerEvents: 'none' }}>
                    {ZERO_WIDTH_SPACE}
                </span>
            ),
    }), [value, placeholder, handlesMap, openContextMenuFromEvent]);

    const isActive = (isOpen && !disabled) || isContextMenuOpen;
    const overlayStateClasses = disabled
        ? 'border-slate-200 bg-slate-100'
        : 'border-slate-300 bg-white';
    const overlayFocusClasses = !disabled && isActive ? 'border-orange-300 shadow-sm ring-2 ring-orange-200' : '';

    return (
        <div className="space-y-2" ref={containerRef}>
            {label && <label className="block text-sm font-medium text-slate-600">{label}</label>}
            <div className="relative">
                <div
                    ref={overlayRef}
                    className={`pointer-events-none absolute inset-0 z-20 whitespace-pre-wrap break-words rounded-md px-3 py-2 text-sm transition duration-150 ${overlayStateClasses} ${overlayFocusClasses} selection:bg-orange-200 selection:text-inherit`}
                    aria-hidden="true"
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
                    className="relative z-10 block w-full resize-none rounded-md border border-transparent bg-transparent px-3 py-2 text-sm text-transparent caret-orange-600 selection:bg-orange-200 selection:text-orange-900 focus:border-transparent focus:outline-none focus:ring-0 disabled:cursor-not-allowed disabled:text-transparent disabled:caret-transparent"
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
                                const displayLabel = suggestion.displayName || suggestion.handle;
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
                                            <span className="font-semibold">{displayLabel}</span>
                                            {entityLabel && (
                                                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                                                    {entityLabel}
                                                </span>
                                            )}
                                        </div>
                                        <div className="mt-1 text-xs text-slate-500">@{suggestion.handle}</div>
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
                {ContextMenu}
            </div>
        </div>
    );
}

function RecordMentionText({
    text,
    entityTypes = null,
    className = '',
    style = {},
    placeholder = '',
    as: Tag = 'span',
}) {
    const containerRef = useRef(null);
    const { handles, refresh } = useMentionDirectory(entityTypes);
    const handlesMap = useMemo(() => buildHandlesMap(handles), [handles]);
    const { ContextMenu, openContextMenuFromEvent } = useMentionContextMenu({ handlesMap, refresh, containerRef });

    const content = useMemo(() => renderMentionContent(text, handlesMap, {
        renderText: (segment, start, end) => (
            <span
                key={`text-${start}-${end}`}
                style={{ pointerEvents: 'none' }}
            >
                {segment || ZERO_WIDTH_SPACE}
            </span>
        ),
        renderMention: ({ handle, metadata, displayLabel, start }) => {
            const badgeLabel = getEntityLabel(metadata);
            return (
                <button
                    key={`mention-${start}-${handle}`}
                    type="button"
                    data-mention-handle={handle}
                    className="pointer-events-auto inline-flex max-w-full items-center gap-1 rounded-full border border-slate-300 bg-slate-50 px-2 py-0.5 text-sm font-medium text-slate-700 transition-colors hover:bg-orange-50 hover:text-orange-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-orange-500"
                    style={{ pointerEvents: 'auto' }}
                    onClick={event => openContextMenuFromEvent(event, handle, metadata)}
                    aria-label={metadata?.displayName ? `Mention: ${metadata.displayName}` : `Mention handle ${handle}`}
                    title={metadata?.displayName ? `${metadata.displayName} (@${handle})` : `@${handle}`}
                >
                    <span className="truncate">{displayLabel}</span>
                    {badgeLabel && (
                        <span className="rounded-full bg-slate-200 px-1 text-[10px] font-semibold uppercase tracking-wide text-slate-600">
                            {badgeLabel}
                        </span>
                    )}
                </button>
            );
        },
        placeholder: placeholder
            ? (
                <span style={{ pointerEvents: 'none' }}>{placeholder}</span>
            )
            : null,
    }), [text, handlesMap, openContextMenuFromEvent, placeholder]);

    return (
        <Tag
            ref={containerRef}
            className={className}
            style={{ whiteSpace: 'pre-wrap', ...style }}
        >
            {content}
            {ContextMenu}
        </Tag>
    );
}

window.RecordMentionComponents = {
    RecordMentionTextarea,
    RecordMentionText,
    useMentionDirectory,
};

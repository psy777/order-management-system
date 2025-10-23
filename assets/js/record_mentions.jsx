const { useCallback, useEffect, useMemo, useRef, useState } = React;

const MENTION_REGEX = /(^|[^a-z0-9_.-])@([a-z0-9_.-]+)/gi;
const ZERO_WIDTH_SPACE = String.fromCharCode(8203);

const MENU_DIMENSIONS = { width: 280, height: 220 };

let caretStylesRegistered = false;
const ensureCaretStyles = () => {
    if (caretStylesRegistered || typeof document === 'undefined') {
        return;
    }
    const style = document.createElement('style');
    style.type = 'text/css';
    style.textContent = `@keyframes recordMentionCaretBlink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}
.record-mention-caret {
  animation: recordMentionCaretBlink 1.2s steps(2, start) infinite;
}`;
    document.head.appendChild(style);
    caretStylesRegistered = true;
};

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

function useMentionDirectory(entityTypes = ['contact', 'firecoast_note']) {
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

const renderMentionContent = (text, handlesMap, {
    renderMention,
    renderText,
    placeholder,
    caretIndex = null,
    renderCaret = null,
}) => {
    const rawValue = text || '';
    const nodes = [];
    const hasCaret = typeof caretIndex === 'number' && caretIndex >= 0;
    let caretInserted = false;

    const pushCaret = index => {
        if (!renderCaret || !hasCaret || caretInserted) {
            return;
        }
        if (index !== caretIndex) {
            return;
        }
        caretInserted = true;
        nodes.push(renderCaret(index));
    };

    const pushTextSegment = (segmentStart, segmentEnd) => {
        if (segmentStart >= segmentEnd) {
            pushCaret(segmentEnd);
            return;
        }
        if (renderCaret && hasCaret && caretIndex > segmentStart && caretIndex < segmentEnd) {
            const before = rawValue.slice(segmentStart, caretIndex);
            if (before) {
                nodes.push(renderText(before, segmentStart, caretIndex));
            }
            pushCaret(caretIndex);
            const after = rawValue.slice(caretIndex, segmentEnd);
            if (after) {
                nodes.push(renderText(after, caretIndex, segmentEnd));
            }
            return;
        }
        const segment = rawValue.slice(segmentStart, segmentEnd);
        nodes.push(renderText(segment, segmentStart, segmentEnd));
        pushCaret(segmentEnd);
    };

    if (!rawValue.trim()) {
        pushCaret(0);
        if (placeholder) {
            nodes.push(placeholder);
        }
        return nodes;
    }

    MENTION_REGEX.lastIndex = 0;
    let lastIndex = 0;
    let match;
    pushCaret(0);
    while ((match = MENTION_REGEX.exec(rawValue)) !== null) {
        const prefixLength = match[1] ? match[1].length : 0;
        const mentionStart = match.index + prefixLength;
        if (mentionStart > lastIndex) {
            pushTextSegment(lastIndex, mentionStart);
        } else {
            pushCaret(mentionStart);
        }
        const handle = normaliseHandle(match[2]);
        const metadata = handlesMap.get(handle) || null;
        const mentionLength = match[2].length + 1;
        const mentionEnd = mentionStart + mentionLength;
        const isCaretInside = renderCaret && hasCaret && caretIndex > mentionStart && caretIndex < mentionEnd;
        const caretOffset = isCaretInside ? caretIndex - mentionStart : null;
        nodes.push(renderMention({
            handle,
            metadata,
            displayLabel: getDisplayLabel(metadata, match[2]),
            start: mentionStart,
            end: mentionEnd,
            isCaretInside,
            caretOffset,
            rawHandle: match[2],
        }));
        pushCaret(mentionEnd);
        lastIndex = mentionEnd;
    }

    if (lastIndex < rawValue.length) {
        pushTextSegment(lastIndex, rawValue.length);
    } else {
        pushCaret(rawValue.length);
    }

    if (nodes.length === 0 && placeholder) {
        nodes.push(placeholder);
    }

    return nodes;
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

    const copyValueToClipboard = useCallback(async (value, label) => {
        if (!value) {
            setMenuFeedback(`No ${label.toLowerCase()} available to copy`);
            setTimeout(() => setMenuFeedback(''), 2000);
            return;
        }
        const textValue = value;
        try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(textValue);
            } else {
                const input = document.createElement('textarea');
                input.value = textValue;
                input.setAttribute('readonly', '');
                input.style.position = 'absolute';
                input.style.left = '-9999px';
                document.body.appendChild(input);
                input.select();
                document.execCommand('copy');
                document.body.removeChild(input);
            }
            setMenuFeedback(`${label} copied to clipboard`);
            setTimeout(() => setMenuFeedback(''), 2000);
        } catch (err) {
            console.error('Failed to copy value', err);
            setMenuFeedback('Unable to copy automatically');
            setTimeout(() => setMenuFeedback(''), 2500);
        }
    }, [setMenuFeedback]);

    const copyHandleToClipboard = useCallback(async handleValue => {
        const handleText = handleValue ? `@${handleValue}` : '';
        await copyValueToClipboard(handleText, 'Handle');
    }, [copyValueToClipboard]);

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
    const contactDetails = contextMetadata && contextMetadata.contact ? contextMetadata.contact : null;
    const contactEmails = Array.isArray(contactDetails?.emails) ? contactDetails.emails : [];
    const contactPhones = Array.isArray(contactDetails?.phones) ? contactDetails.phones : [];
    const contactAddresses = Array.isArray(contactDetails?.addresses) ? contactDetails.addresses : [];
    const hasAnyContactInfo = contactDetails
        ? Boolean(
            (contactDetails.contactName && contactDetails.contactName.trim()) ||
            (contactDetails.companyName && contactDetails.companyName.trim()) ||
            (contactDetails.email && contactDetails.email.toString().trim()) ||
            (contactDetails.phone && contactDetails.phone.toString().trim()) ||
            contactEmails.some(entry => entry && entry.value) ||
            contactPhones.some(entry => entry && entry.value) ||
            contactAddresses.length > 0
        )
        : false;

    const renderCopyTile = (label, value, options = {}) => {
        if (!value) {
            return null;
        }
        const {
            badge = '',
            lines = null,
            displayValue = null,
            key = null,
        } = options;
        const displayLines = Array.isArray(lines) && lines.length > 0 ? lines.filter(Boolean) : null;
        const resolvedDisplay = displayValue != null ? displayValue : value;
        const handleCopy = () => copyValueToClipboard(value, label);
        return (
            <button
                key={key ?? undefined}
                type="button"
                className="record-mention-contact-tile"
                onClick={handleCopy}
            >
                <div className="record-mention-contact-tile__header">
                    <div className="record-mention-contact-tile__label">{label}</div>
                    {badge && (
                        <span className="record-mention-contact-tile__badge">
                            {badge}
                        </span>
                    )}
                </div>
                <div className="record-mention-contact-tile__value">
                    {displayLines ? (
                        displayLines.map((line, index) => (
                            <span key={`${label}-line-${index}`} className="block break-words">
                                {line}
                            </span>
                        ))
                    ) : Array.isArray(resolvedDisplay) ? (
                        resolvedDisplay.filter(Boolean).map((line, index) => (
                            <span key={`${label}-display-${index}`} className="block break-words">
                                {line}
                            </span>
                        ))
                    ) : (
                        <span className="break-words">{resolvedDisplay}</span>
                    )}
                </div>
                <div className="record-mention-contact-tile__hint">Click to copy</div>
            </button>
        );
    };

    const ContextMenu = contextMenu.isOpen ? (
        <div
            ref={menuRef}
            className="fixed z-50 w-64 rounded-xl border border-slate-200 bg-white p-4 shadow-2xl ring-1 ring-slate-100"
            style={{ top: contextMenu.position.top, left: contextMenu.position.left }}
        >
            <div className="record-mention-context-header">
                <button
                    type="button"
                    className="record-mention-context-name"
                    onClick={() => {
                        if (!recordUrl) {
                            setMenuFeedback('No record destination yet.');
                            setTimeout(() => setMenuFeedback(''), 2000);
                            return;
                        }
                        window.open(recordUrl, '_blank');
                        closeContextMenu();
                    }}
                >
                    {getDisplayLabel(contextMetadata, resolvedHandleForDisplay)}
                </button>
                <button
                    type="button"
                    className="record-mention-context-handle"
                    onClick={() => copyHandleToClipboard(resolvedHandleForDisplay)}
                >
                    @{resolvedHandleForDisplay}
                </button>
                {contextEntityLabel && (
                    <span className="record-mention-context-pill">
                        {contextEntityLabel}
                    </span>
                )}
            </div>
            {contactDetails ? (
                <div className="record-mention-contact-grid">
                    {renderCopyTile('Contact', contactDetails.contactName)}
                    {renderCopyTile('Company', contactDetails.companyName)}
                    {renderCopyTile(contactDetails.emailLabel || 'Email', contactDetails.emailValue || contactDetails.email, {
                        badge: contactDetails.emailIsPrimary ? 'Primary' : '',
                        displayValue: contactDetails.email,
                    })}
                    {contactEmails
                        .filter(entry => entry?.value && entry.value !== (contactDetails.emailValue || contactDetails.email))
                        .map((entry, index) => renderCopyTile(entry.label || 'Email', entry.value, {
                            badge: entry.isPrimary ? 'Primary' : '',
                            displayValue: entry.value,
                            key: `contact-email-${index}`,
                        }))}
                    {renderCopyTile(contactDetails.phoneLabel || 'Phone', contactDetails.phoneValue || contactDetails.phone, {
                        badge: contactDetails.phoneIsPrimary ? 'Primary' : '',
                        displayValue: contactDetails.phone,
                    })}
                    {contactPhones
                        .filter(entry => entry?.value && entry.value !== (contactDetails.phoneValue || contactDetails.phone))
                        .map((entry, index) => renderCopyTile(entry.label || 'Phone', entry.value, {
                            badge: entry.isPrimary ? 'Primary' : '',
                            displayValue: entry.formatted || entry.value,
                            key: `contact-phone-${index}`,
                        }))}
                    {contactAddresses.map((entry, index) => renderCopyTile(entry.label || 'Address', entry.value, {
                        badge: entry.isPrimary ? 'Primary' : '',
                        lines: entry.lines,
                        key: `contact-address-${index}`,
                    }))}
                    {!hasAnyContactInfo && (
                        <div className="record-mention-contact-empty">
                            No saved contact details yet.
                        </div>
                    )}
                </div>
            ) : (
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
            )}
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
    onSubmit = null,
    disabled = false,
    rows = 3,
    entityTypes = ['contact'],
    className = '',
    textareaClassName = '',
}) {
    ensureCaretStyles();
    const containerRef = useRef(null);
    const textareaRef = useRef(null);
    const overlayRef = useRef(null);
    const [suggestions, setSuggestions] = useState([]);
    const [isOpen, setIsOpen] = useState(false);
    const [isFocused, setIsFocused] = useState(false);
    const [highlightIndex, setHighlightIndex] = useState(0);
    const [selectionRange, setSelectionRange] = useState({
        start: value ? value.length : 0,
        end: value ? value.length : 0,
    });
    const lastCaretRef = useRef(value ? value.length : 0);
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

    const updateSelectionFromTextarea = useCallback(() => {
        const textarea = textareaRef.current;
        if (!textarea) return;
        const start = textarea.selectionStart ?? 0;
        const end = textarea.selectionEnd ?? start;
        lastCaretRef.current = start;
        setSelectionRange(prev => {
            if (prev.start === start && prev.end === end) {
                return prev;
            }
            return { start, end };
        });
    }, []);

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

    useEffect(() => {
        requestAnimationFrame(() => updateSelectionFromTextarea());
    }, [value, updateSelectionFromTextarea]);

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
        setSelectionRange(prev => {
            if (prev.start === caret && prev.end === caret) {
                return prev;
            }
            return { start: caret, end: caret };
        });
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
        const nextChar = text.slice(mentionEnd, mentionEnd + 1);
        const needsTrailingSpace = !nextChar || !/\s/.test(nextChar);
        const replacement = `@${suggestion.handle}${needsTrailingSpace ? ' ' : ''}`;
        const newValue = text.slice(0, mentionStart) + replacement + text.slice(mentionEnd);
        onChange(newValue);
        requestAnimationFrame(() => {
            const newCaret = mentionStart + replacement.length;
            textarea.setSelectionRange(newCaret, newCaret);
            textarea.focus();
            updateSelectionFromTextarea();
        });
        closeSuggestions();
        closeContextMenu();
    };

    const handleKeyDown = event => {
        requestAnimationFrame(() => updateSelectionFromTextarea());
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
        if (isOpen && suggestions.length > 0) {
            if (event.key === 'ArrowDown') {
                event.preventDefault();
                setHighlightIndex(prev => (prev + 1) % suggestions.length);
                return;
            }
            if (event.key === 'ArrowUp') {
                event.preventDefault();
                setHighlightIndex(prev => (prev - 1 + suggestions.length) % suggestions.length);
                return;
            }
            if (event.key === 'Enter' || event.key === 'Tab') {
                event.preventDefault();
                const suggestion = suggestions[highlightIndex] || suggestions[0];
                if (suggestion) {
                    replaceWithSuggestion(suggestion);
                }
                return;
            }
        }
        if (event.key === 'Enter' && !event.shiftKey) {
            if (typeof onSubmit === 'function') {
                event.preventDefault();
                const textarea = textareaRef.current;
                onSubmit(textarea ? textarea.value : event.target.value);
            }
        }
    };

    const handleInput = event => {
        const text = event.target.value;
        onChange(text);
        closeContextMenu();
        computeSuggestions(text, event.target.selectionStart);
        requestAnimationFrame(() => updateSelectionFromTextarea());
    };

    const handleBlur = () => {
        setIsFocused(false);
        setTimeout(() => closeSuggestions(), 150);
    };

    const handleKeyUp = event => {
        requestAnimationFrame(() => {
            updateSelectionFromTextarea();
            const textarea = textareaRef.current;
            if (!textarea) {
                return;
            }
            if (
                event.key === 'ArrowLeft' ||
                event.key === 'ArrowRight' ||
                event.key === 'Home' ||
                event.key === 'End' ||
                event.key === 'Backspace' ||
                event.key === 'Delete'
            ) {
                computeSuggestions(textarea.value, textarea.selectionStart);
            }
        });
    };

    const handleClick = () => {
        requestAnimationFrame(() => updateSelectionFromTextarea());
    };

    const handleFocus = () => {
        setIsFocused(true);
        requestAnimationFrame(() => updateSelectionFromTextarea());
    };

    const handleSelect = () => {
        requestAnimationFrame(() => updateSelectionFromTextarea());
    };

    const highlightNodes = useMemo(() => renderMentionContent(value, handlesMap, {
        caretIndex: selectionRange.start,
        renderCaret: index => (
            <span
                key={`caret-${index}-${selectionRange.start}-${selectionRange.end}`}
                className="record-mention-caret inline-block h-[1.2em] w-px translate-y-[1px] bg-orange-500 align-middle"
            />
        ),
        renderText: (segment, start, end) => (
            <span
                key={`text-${start}-${end}`}
                className="text-sm text-slate-700"
                style={{ pointerEvents: 'none' }}
            >
                {segment || ZERO_WIDTH_SPACE}
            </span>
        ),
        renderMention: ({ handle, metadata, displayLabel, start, end, isCaretInside, caretOffset, rawHandle }) => {
            const badgeLabel = getEntityLabel(metadata);
            const mentionText = `@${rawHandle || handle}`;
            const pillLabel = isCaretInside ? mentionText : displayLabel;
            const pillClasses = isCaretInside
                ? 'mention-pill pointer-events-auto inline-flex max-w-full items-center gap-1 rounded-full border border-orange-300 bg-orange-100 px-2 py-0.5 text-xs font-semibold text-orange-700 shadow-sm'
                : 'mention-pill pointer-events-auto inline-flex max-w-full items-center gap-1 rounded-full border border-orange-200 bg-orange-50 px-2 py-0.5 text-xs font-semibold text-orange-700 shadow-sm transition-colors hover:bg-orange-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-orange-500';
            const caretWithinMention = isCaretInside && typeof caretOffset === 'number'
                ? Math.max(0, Math.min(mentionText.length, caretOffset))
                : null;
            return (
                <button
                    key={`mention-${start}-${handle}`}
                    type="button"
                    data-mention-handle={handle}
                    tabIndex={-1}
                    style={{ pointerEvents: 'auto' }}
                    className={pillClasses}
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
                    {isCaretInside ? (
                        <span className="flex min-w-0 items-center gap-0.5">
                            <span className="truncate">
                                {caretWithinMention ? mentionText.slice(0, caretWithinMention) : ZERO_WIDTH_SPACE}
                            </span>
                            <span className="record-mention-caret inline-block h-[1.2em] w-px bg-orange-500" />
                            <span className="truncate">
                                {mentionText.slice(caretWithinMention ?? 0) || ZERO_WIDTH_SPACE}
                            </span>
                        </span>
                    ) : (
                        <span className="truncate">{pillLabel}</span>
                    )}
                    {!isCaretInside && badgeLabel && (
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
    }), [value, placeholder, handlesMap, openContextMenuFromEvent, selectionRange.start, selectionRange.end]);

    const isActive = (!disabled && isFocused) || (isOpen && !disabled) || isContextMenuOpen;
    const overlayStateClasses = disabled
        ? 'border-slate-200 bg-slate-100'
        : 'border-slate-300 bg-white';
    const overlayFocusClasses = !disabled && isActive ? 'border-orange-300 shadow-sm ring-2 ring-orange-200' : '';

    return (
        <div
            className={`record-mention-textarea space-y-2 ${className}`.trim()}
            ref={containerRef}
        >
            {label && <label className="block text-sm font-medium text-slate-600">{label}</label>}
            <div className="relative">
                <div
                    ref={overlayRef}
                    className={`record-mention-textarea__overlay pointer-events-none absolute inset-0 z-20 whitespace-pre-wrap break-words rounded-md px-3 py-2 text-sm transition duration-150 ${overlayStateClasses} ${overlayFocusClasses} selection:bg-orange-200 selection:text-inherit`}
                    aria-hidden="true"
                >
                    {highlightNodes}
                </div>
                <textarea
                    ref={textareaRef}
                    value={value || ''}
                    onChange={handleInput}
                    onKeyDown={handleKeyDown}
                    onKeyUp={handleKeyUp}
                    onClick={handleClick}
                    onBlur={handleBlur}
                    onFocus={handleFocus}
                    onSelect={handleSelect}
                    onScroll={syncOverlayScroll}
                    placeholder={placeholder}
                    disabled={disabled}
                    rows={rows}
                    className={`record-mention-textarea__input relative z-10 block w-full resize-none rounded-md border-0 bg-transparent px-3 py-2 text-sm text-transparent caret-transparent selection:bg-orange-200 selection:text-orange-900 focus:outline-none focus:ring-0 disabled:cursor-not-allowed disabled:text-transparent disabled:caret-transparent ${textareaClassName}`.trim()}
                />
                {isOpen && (
                    <div className="record-mention-textarea__menu absolute bottom-full left-0 right-0 z-30 mb-2">
                        <ul className="record-mention-textarea__list max-h-60 overflow-auto rounded-xl border border-slate-200 bg-white shadow-2xl ring-1 ring-slate-100" role="listbox">
                            <li className="record-mention-textarea__header border-b border-slate-100 bg-slate-50 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
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
                                        className={`record-mention-textarea__option cursor-pointer px-4 py-3 text-sm transition-colors ${isHighlighted ? 'is-active' : ''}`}
                                        onMouseDown={event => {
                                            event.preventDefault();
                                            replaceWithSuggestion(suggestion);
                                        }}
                                        onMouseEnter={() => setHighlightIndex(index)}
                                    >
                                        <div className="record-mention-textarea__option-main flex items-center justify-between gap-2">
                                            <span className="record-mention-textarea__item-label font-semibold">{displayLabel}</span>
                                            {entityLabel && (
                                                <span className="record-mention-textarea__item-entity rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                                                    {entityLabel}
                                                </span>
                                            )}
                                        </div>
                                        <div className="record-mention-textarea__item-handle mt-1 text-xs text-slate-500">@{suggestion.handle}</div>
                                    </li>
                                );
                            })}
                            {suggestions.length === 0 && !isLoading && !error && (
                                <li className="record-mention-textarea__empty px-4 py-3 text-sm text-slate-500">
                                    No matching records yet. Try typing more or refresh the directory.
                                </li>
                            )}
                            {error && (
                                <li className="record-mention-textarea__error px-4 py-3 text-sm text-red-600">Unable to load mention directory. Please refresh.</li>
                            )}
                            {isLoading && (
                                <li className="record-mention-textarea__loading px-4 py-3 text-sm text-slate-500">Loading directory…</li>
                            )}
                            <li className="record-mention-textarea__footer border-t border-slate-100 bg-white px-4 py-2">
                                <button
                                    type="button"
                                    className="record-mention-textarea__refresh text-sm font-semibold text-orange-600 transition hover:text-orange-700"
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
            className={`record-mention-text ${className}`.trim()}
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

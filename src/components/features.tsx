import React, { useState, useEffect, useRef } from 'react';
import { jsPDF } from 'jspdf';
import 'jspdf-autotable';
import { Chart } from 'chart.js';
import { Card, Input, Select, Textarea, TrashIcon, PdfIcon, EmailIcon, TrendingUpIcon, CogIcon } from './ui';
import { generatePdf } from '../lib/pdf';

export const SalesChart = ({ data }: { data: any }) => {
    const chartRef = useRef<HTMLCanvasElement>(null);
    const chartInstance = useRef<Chart | null>(null);
    useEffect(() => {
        if (chartRef.current) {
            if (chartInstance.current) {
                chartInstance.current.destroy();
            }
            const ctx = chartRef.current.getContext('2d');
            if (ctx) {
                chartInstance.current = new Chart(ctx, {
                    type: 'bar',
                    data,
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        scales: { y: { beginAtZero: true } },
                        plugins: { legend: { display: false } }
                    }
                });
            }
        }
        return () => {
            if (chartInstance.current) {
                chartInstance.current.destroy();
            }
        };
    }, [data]);
    return <canvas ref={chartRef} />;
};

export const SignaturePad = ({ onSave, initialDataUrl, disabled = false }: { onSave: (dataUrl: string | null) => void, initialDataUrl?: string | null, disabled?: boolean }) => {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const [isDrawing, setIsDrawing] = useState(false);
    const [signatureData, setSignatureData] = useState(initialDataUrl || null);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        
        canvas.width = canvas.offsetWidth;
        canvas.height = canvas.offsetHeight;

        if (ctx) {
            ctx.strokeStyle = '#000000';
            ctx.lineWidth = 2;
            ctx.lineCap = 'round';
            ctx.lineJoin = 'round';

            if (signatureData) {
                const img = new Image();
                img.onload = () => {
                    if (ctx) {
                        ctx.clearRect(0, 0, canvas.width, canvas.height);
                        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
                    }
                };
                img.src = signatureData;
            } else {
                ctx.clearRect(0, 0, canvas.width, canvas.height);
            }
        }
    }, [signatureData, disabled]);

    const getMousePos = (canvas: HTMLCanvasElement, evt: React.MouseEvent) => {
        const rect = canvas.getBoundingClientRect();
        return {
            x: evt.clientX - rect.left,
            y: evt.clientY - rect.top
        };
    };
    
    const getTouchPos = (canvas: HTMLCanvasElement, touch: React.Touch) => {
        const rect = canvas.getBoundingClientRect();
        return {
            x: touch.clientX - rect.left,
            y: touch.clientY - rect.top
        };
    };

    const startDrawing = (e: React.MouseEvent | React.TouchEvent) => {
        if (disabled) return;
        setIsDrawing(true);
        const canvas = canvasRef.current;
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        const pos = 'touches' in e ? getTouchPos(canvas, (e as React.TouchEvent).touches[0]) : getMousePos(canvas, e as React.MouseEvent);
        ctx.beginPath();
        ctx.moveTo(pos.x, pos.y);
        e.preventDefault();
    };

    const draw = (e: React.MouseEvent | React.TouchEvent) => {
        if (!isDrawing || disabled) return;
        const canvas = canvasRef.current;
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        const pos = 'touches' in e ? getTouchPos(canvas, (e as React.TouchEvent).touches[0]) : getMousePos(canvas, e as React.MouseEvent);
        ctx.lineTo(pos.x, pos.y);
        ctx.stroke();
        e.preventDefault();
    };

    const stopDrawing = () => {
        if (!isDrawing || disabled) return;
        setIsDrawing(false);
        const canvas = canvasRef.current;
        if (!canvas) return;
        const dataUrl = canvas.toDataURL('image/png');
        setSignatureData(dataUrl);
        if (onSave) {
            onSave(dataUrl);
        }
    };

    const clearSignature = () => {
        if (disabled) return;
        const canvas = canvasRef.current;
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        setSignatureData(null);
        if (onSave) {
            onSave(null);
        }
    };
    
    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas || disabled) return;

        const handleTouchStart = (e: TouchEvent) => startDrawing(e as unknown as React.TouchEvent);
        const handleTouchMove = (e: TouchEvent) => draw(e as unknown as React.TouchEvent);

        canvas.addEventListener('touchstart', handleTouchStart, { passive: false });
        canvas.addEventListener('touchmove', handleTouchMove, { passive: false });
        canvas.addEventListener('touchend', stopDrawing);
        canvas.addEventListener('touchcancel', stopDrawing);

        return () => {
            canvas.removeEventListener('touchstart', handleTouchStart);
            canvas.removeEventListener('touchmove', handleTouchMove);
            canvas.removeEventListener('touchend', stopDrawing);
            canvas.removeEventListener('touchcancel', stopDrawing);
        };
    }, [isDrawing, disabled, onSave]);

    const canvasStyle: React.CSSProperties = {
        touchAction: 'none'
    };

    return (
        <div className="space-y-2">
            <canvas
                ref={canvasRef}
                onMouseDown={startDrawing}
                onMouseMove={draw}
                onMouseUp={stopDrawing}
                onMouseLeave={stopDrawing}
                className={`w-full h-40 bg-slate-100 border border-slate-300 rounded-md cursor-crosshair ${disabled ? 'cursor-not-allowed opacity-70' : ''}`}
                style={canvasStyle}
            ></canvas>
            {!disabled && (
                <button
                    onClick={clearSignature}
                    className="w-full text-center px-4 py-2 bg-slate-200 text-slate-700 font-semibold rounded-md hover:bg-slate-300 transition-colors text-sm"
                >
                    Clear Signature
                </button>
            )}
        </div>
    );
};

export const EmailModal = ({ order, onClose, allItems, onOrderSent, appSettings }: { order: any, onClose: () => void, allItems: any, onOrderSent: (order: any) => void, appSettings: any }) => {
    const fileInputRef = useRef<HTMLInputElement>(null);
    const [attachments, setAttachments] = useState<File[]>([]);
    const [isSending, setIsSending] = useState(false);
    const [emailSent, setEmailSent] = useState(false);
    const [editableBody, setEditableBody] = useState('');

    const hiddenFileInputStyle: React.CSSProperties = { display: 'none' };

    const handleFileSelected = (event: React.ChangeEvent<HTMLInputElement>) => {
        const files = Array.from(event.target.files || []);
        if (files.length > 0) {
            setAttachments(prev => [...prev, ...files]);
        }
        // Reset file input so the same file can be selected again
        if (fileInputRef.current) {
            fileInputRef.current.value = "";
        }
    };

    useEffect(() => {
        const handleOutsideClick = (event: MouseEvent) => {
            if ((event.target as HTMLElement).id === "email-modal-backdrop") {
                onClose();
            }
        };

        document.addEventListener('mousedown', handleOutsideClick);

        return () => {
            document.removeEventListener('mousedown', handleOutsideClick);
        };
    }, [onClose]);

    useEffect(() => {
        if (order && appSettings && appSettings.default_email_body) {
            let body = appSettings.default_email_body;
            body = body.replace(/\[customerName\]/g, order.vendorInfo.contactName || order.vendorInfo.companyName || '');
            body = body.replace(/\[vendorCompanyName\]/g, order.vendorInfo.companyName || '');
            body = body.replace(/\[orderID\]/g, order.id || '');
            body = body.replace(/\[yourCompany\]/g, appSettings.company_name || 'Your Company');
            setEditableBody(body);
        } else if (order) {
            const fallbackYourCompanyName = appSettings?.company_name || "Your Company";
            setEditableBody(
`Dear ${order.vendorInfo.contactName || order.vendorInfo.companyName},

Please find attached the purchase order ${order.id} for your records.

Thank you,
${fallbackYourCompanyName}`
            );
        }
    }, [order, appSettings]);

    if (!order) return null;

    const recipient = order.vendorInfo.email;
    const subjectText = `${appSettings?.company_name || "Your Company"} - Order Confirmation ${order.id}`;
    
    return (
    <div id="email-modal-backdrop" className="fixed inset-0 bg-black bg-opacity-50 z-50 flex justify-center items-center p-4">
        <div className="bg-white rounded-lg shadow-xl p-8 w-full max-w-2xl max-h-[90vh] overflow-y-auto modal-content">
                <h2 className="text-2xl font-bold text-slate-800 mb-4">Send Order Confirmation</h2>
                <div className="space-y-4">
                    <Input label="To" value={order.vendorInfo.email} disabled onChange={() => {}} />
                    <Input label="Subject" value={subjectText} disabled onChange={() => {}} />
                    <Textarea label="Email Body" value={editableBody} onChange={e => setEditableBody(e.target.value)} rows={8} disabled={false} />
                    <div className="bg-orange-50 p-4 rounded-md border border-orange-200">
                        <p className="text-sm font-medium text-orange-700 mb-2">Order PDF:</p>
                        <button
                            onClick={() => {
                                if (order && allItems) {
                                    try {
                                        generatePdf(order, allItems, 'save');
                                    } catch (e) {
                                        console.error("Error generating PDF for download:", e);
                                        alert("Failed to generate PDF for download.");
                                    }
                                } else {
                                    alert("Order data or item data is missing, cannot generate PDF.");
                                }
                            }}
                            className="w-full flex items-center justify-center bg-blue-600 hover:bg-blue-700 text-white font-semibold px-4 py-3 rounded-md shadow-sm transition-colors"
                        >
                            <PdfIcon />
                            <span className="ml-2">Download PO_{order.id}.pdf</span>
                        </button>
                        <p className="mt-2 text-xs text-orange-700">Click to download the PO. Then, use the 'Upload Custom Attachment' button below if you wish to attach it to the email.</p>
                    </div>

                    <div className="pt-2">
                        <input
                            type="file"
                            ref={fileInputRef}
                            style={hiddenFileInputStyle}
                            onChange={handleFileSelected}
                            multiple
                        />
                        <button
                            onClick={() => fileInputRef.current && fileInputRef.current.click()}
                            className={`w-full flex items-center justify-center bg-slate-200 hover:bg-slate-300 text-slate-700 font-semibold px-4 py-3 rounded-md shadow-sm transition-colors text-sm ${isSending ? 'opacity-50 cursor-not-allowed' : ''}`}
                            disabled={isSending}
                        >
                            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5 mr-2" viewBox="0 0 20 20" fill="currentColor">
                                <path fillRule="evenodd" d="M3 17a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zM6.293 6.707a1 1 0 010-1.414l3-3a1 1 0 011.414 0l3 3a1 1 0 01-1.414 1.414L11 5.414V13a1 1 0 11-2 0V5.414L7.707 6.707a1 1 0 01-1.414 0z" clipRule="evenodd" />
                            </svg>
                            {isSending ? 'Processing...' : 'Upload Attachments'}
                        </button>
                        {attachments.length > 0 && (
                            <div className="mt-3 space-y-2">
                                <p className="text-sm font-medium text-slate-700">Attached Files:</p>
                                <ul className="list-disc list-inside bg-slate-50 p-3 rounded-md border border-slate-200">
                                    {attachments.map((file, index) => {
                                        const expectedPoName = `PO_${order.id}.pdf`;
                                        const isMatch = file.name === expectedPoName;
                                        return (
                                            <li key={index} className="text-xs flex justify-between items-center">
                                                <span className={isMatch ? 'text-green-600 font-semibold' : 'text-slate-600'}>
                                                    {file.name}
                                                    {isMatch && <span className="ml-1">✔ Matches Order</span>}
                                                </span>
                                                <button onClick={() => setAttachments(attachments.filter((_, i) => i !== index))} className="text-red-500 hover:text-red-700 p-1">
                                                    <TrashIcon />
                                                </button>
                                            </li>
                                        );
                                    })}
                                </ul>
                            </div>
                        )}
                    </div>
                </div>
                <div className="mt-6 flex items-center justify-between">
                    <button 
                        onClick={onClose} 
                        className="px-4 py-2 bg-slate-200 text-slate-700 font-semibold rounded-md hover:bg-slate-300 transition-colors"
                    >
                        Cancel
                    </button>
                    <button
                        onClick={async () => {
                            if (isSending) return;

                            const hasMismatchedPo = attachments.some(file => file.name !== `PO_${order.id}.pdf`);
                            if (attachments.length === 0) {
                                if (!window.confirm("No files are attached. Send email without attachments?")) return;
                            } else if (hasMismatchedPo) {
                                const expectedPoName = `PO_${order.id}.pdf`;
                                if (!window.confirm(`Warning: At least one attachment does not match the expected PO name "${expectedPoName}". Proceed anyway?`)) return;
                            }
                            
                            setIsSending(true);
                            
                            const formData = new FormData();
                            formData.append('order', JSON.stringify(order));
                            formData.append('recipientEmail', order.vendorInfo.email);
                            formData.append('subject', subjectText);
                            formData.append('body', editableBody);
                            attachments.forEach(file => {
                                formData.append('attachments', file);
                            });

                            try {
                                const response = await fetch('/api/send-order-email', {
                                    method: 'POST',
                                    body: formData,
                                });
                                
                                const result = await response.json();
                                if (response.ok) {
                                    setEmailSent(true);
                                    
                                    const newStatusHistory = [...order.statusHistory, { status: 'Sent', date: new Date().toISOString() }];
                                    const updatedOrderForStatusChange = { ...order, status: 'Sent', statusHistory: newStatusHistory };
                                    
                                    onOrderSent(updatedOrderForStatusChange);

                                    setTimeout(() => {
                                        onClose();
                                    }, 1000);
                                } else {
                                    alert(`Failed to send email: ${result.message || 'Server error'}`);
                                }
                            } catch (error: any) {
                                alert(`Error sending email: ${error.message}`);
                            } finally {
                                setIsSending(false);
                            }
                        }}
                        className={`px-6 py-2 bg-green-600 text-white font-semibold rounded-md hover:bg-green-700 transition-colors ${isSending || emailSent ? 'opacity-50 cursor-not-allowed' : ''}`}
                        disabled={isSending || emailSent}
                    >
                        {emailSent ? 'Sent!' : isSending ? 'Sending...' : 'Send via Server'}
                    </button>
                </div>
            </div>
        </div>
    );
};

export const ShippedStatusBadge = ({ statusText }: { statusText: string }) => {
    const badgeRef = useRef<HTMLSpanElement>(null);
    const [glintStyle, setGlintStyle] = useState<React.CSSProperties>({});
    const proximityThreshold = 75;

    const baseStyle: React.CSSProperties = {
        background: `linear-gradient(135deg, #FDE047, #FBBF24, #F59E0B)`,
    };

    useEffect(() => {
        setGlintStyle(baseStyle);

        const handleGlobalMouseMove = (e: MouseEvent) => {
            if (!badgeRef.current) return;

            const rect = badgeRef.current.getBoundingClientRect();
            const badgeCenterX = rect.left + rect.width / 2;
            const badgeCenterY = rect.top + rect.height / 2;

            const cursorX = e.clientX;
            const cursorY = e.clientY;

            const distanceX = cursorX - badgeCenterX;
            const distanceY = cursorY - badgeCenterY;
            const distance = Math.sqrt(distanceX * distanceX + distanceY * distanceY);

            if (distance < proximityThreshold + rect.width / 2) {
                const xInBadge = cursorX - rect.left;
                const yInBadge = cursorY - rect.top;

                const glareX = (xInBadge / rect.width) * 100;
                const glareY = (yInBadge / rect.height) * 100;
                
                const clampedGlareX = Math.max(-50, Math.min(150, glareX));
                const clampedGlareY = Math.max(-50, Math.min(150, glareY));

                setGlintStyle({
                    background: `radial-gradient(circle at ${clampedGlareX}% ${clampedGlareY}%, rgba(255,255,255,0.75) 0%, rgba(255,255,255,0) 50%), linear-gradient(135deg, #FDE047, #FBBF24, #F59E0B)`,
                    transition: 'background 0.05s linear'
                });
            } else {
                setGlintStyle({...baseStyle, transition: 'background 0.3s ease-out'});
            }
        };

        document.addEventListener('mousemove', handleGlobalMouseMove);
        return () => {
            document.removeEventListener('mousemove', handleGlobalMouseMove);
        };
    }, []);

    return (
        <span
            ref={badgeRef}
            className="px-2 py-1 text-xs font-semibold rounded-full text-amber-800 border-2 border-amber-500 shadow-md"
            style={{...glintStyle, display: 'inline-block', position: 'relative', overflow: 'hidden' }}
        >
            {statusText}
        </span>
    );
};

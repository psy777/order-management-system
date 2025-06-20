import { jsPDF } from "jspdf";
import "jspdf-autotable";

declare module "jspdf" {
    interface jsPDF {
        lastAutoTable: {
            finalY: number;
        };
        autoTable: (options: any) => jsPDF;
    }
}

export const generatePdf = (order: any, allItems: any, action = 'save') => {
    const doc = new jsPDF();
    const { vendorInfo, lineItems, notes, total, id, date, scentOption, estimatedShippingDate, estimatedShipping, nameDrop, signatureDataUrl } = order;
    doc.setFontSize(20); doc.text("Purchase Order", 14, 22);
    doc.setFontSize(12); doc.text(`PO #: ${id}`, 14, 30); doc.text(`Date: ${new Date(date).toLocaleDateString()}`, 14, 36);
    
    let leftColumnY = 50;
    let rightColumnY = 50;
    const leftColumnX = 14;
    const rightColumnX = 105;

    doc.setFontSize(12); doc.text("Vendor Information", leftColumnX, leftColumnY); 
    leftColumnY += 6;
    doc.setFontSize(10);
    doc.text(`${vendorInfo.companyName}`, leftColumnX, leftColumnY); leftColumnY += 5;
    doc.text(`${vendorInfo.contactName}`, leftColumnX, leftColumnY); leftColumnY += 5;
    doc.text(`${vendorInfo.email}`, leftColumnX, leftColumnY); leftColumnY += 5;
    doc.text(`${vendorInfo.phone}`, leftColumnX, leftColumnY); leftColumnY += 5;

    if (vendorInfo.billingAddress || vendorInfo.billingCity) {
        doc.setFontSize(10);
        doc.text("Billing Address:", rightColumnX, rightColumnY); rightColumnY += 5;
        const ba = `${vendorInfo.billingAddress || ''}\n${vendorInfo.billingCity || ''}, ${vendorInfo.billingState || ''} ${vendorInfo.billingZipCode || ''}`;
        const billingAddressLines = doc.splitTextToSize(ba, 90);
        doc.text(billingAddressLines, rightColumnX, rightColumnY);
        rightColumnY += (billingAddressLines.length * 4) + 2;
    }

    if (vendorInfo.shippingAddress || vendorInfo.shippingCity) {
        if (vendorInfo.billingAddress || vendorInfo.billingCity) rightColumnY += 3;
        doc.setFontSize(10);
        doc.text("Shipping Address:", rightColumnX, rightColumnY); rightColumnY += 5;
        const sa = `${vendorInfo.shippingAddress || ''}\n${vendorInfo.shippingCity || ''}, ${vendorInfo.shippingState || ''} ${vendorInfo.shippingZipCode || ''}`;
        const shippingAddressLines = doc.splitTextToSize(sa, 90);
        doc.text(shippingAddressLines, rightColumnX, rightColumnY);
        rightColumnY += (shippingAddressLines.length * 4) + 2;
    }
    
    let tableStartY = Math.max(leftColumnY, rightColumnY, 70);
    tableStartY += 5;

    const tableColumn = ["Item", "Style", "Quantity", "Unit Price", "Total"];
    const tableRows = lineItems.map((item: any) => [allItems[item.item]?.name || item.item, item.style, item.quantity, `$${(item.price / 100).toFixed(2)}`, `$${((item.quantity * item.price) / 100).toFixed(2)}`]);
    doc.autoTable({ head: [tableColumn], body: tableRows, startY: tableStartY });

    let calculatedSubtotal = 0;
    lineItems.forEach((item: any) => {
        calculatedSubtotal += item.quantity * item.price;
    });

    let calculatedNameDropSurcharge = 0;
    if (nameDrop) {
        lineItems.forEach((item: any) => {
            if (item.type === 'cross') {
                calculatedNameDropSurcharge += item.quantity * 100;
            }
        });
    }

    let finalY = doc.lastAutoTable.finalY + 10;
    doc.setFontSize(10);
    doc.text(`Subtotal: $${(calculatedSubtotal / 100).toFixed(2)}`, 14, finalY);
    finalY += 7;

    if (calculatedNameDropSurcharge > 0) {
        doc.text(`Name Drop Surcharge: $${(calculatedNameDropSurcharge / 100).toFixed(2)}`, 14, finalY);
        finalY += 7;
    }

    const estShippingValue = parseFloat(estimatedShipping) || 0;
    if (estShippingValue > 0) {
        doc.setFontSize(10);
        doc.text(`Est. Shipping Cost: $${estShippingValue.toFixed(2)}`, 14, finalY);
        finalY += 7;
    }

    const estimatedShippingInCentsForPdfTotal = Math.round(estShippingValue * 100);
    const grandTotalForPdfDisplay = calculatedSubtotal + calculatedNameDropSurcharge + estimatedShippingInCentsForPdfTotal;

    doc.setFontSize(12);
    doc.text(`Total: $${(grandTotalForPdfDisplay / 100).toFixed(2)}`, 14, finalY);
    
    finalY += 10; 
    doc.setFontSize(10); 
    doc.text(`Scent Option: ${scentOption}`, 14, finalY);

    let currentLineY = finalY + 5;

    if(estimatedShippingDate) {
        doc.text(`Est. Ship Date: ${new Date(estimatedShippingDate + 'T00:00:00').toLocaleDateString()}`, 14, currentLineY);
        currentLineY += 5;
    }
    
    finalY = currentLineY - 5;
    
    let notesStartY = finalY + 10;
    if (notes) { 
        doc.text("Notes:", 14, notesStartY); 
        const splitNotes = doc.splitTextToSize(notes, 180); 
        doc.text(splitNotes, 14, notesStartY + 5);
        finalY = notesStartY + 5 + (splitNotes.length * 4);
    } else {
        finalY = notesStartY;
    }

    finalY += 15;
    if (finalY > 260) {
        doc.addPage();
        finalY = 20;
    }
    doc.setFontSize(10);
    doc.text("Authorized Signature:", 14, finalY);

    if (signatureDataUrl) {
        try {
            if (signatureDataUrl.startsWith('data:image/png;base64,')) {
                const base64ImageData = signatureDataUrl.substring(signatureDataUrl.indexOf(',') + 1);
                if (base64ImageData.length > 150) { 
                    const signatureImgWidth = 70;
                    const signatureImgHeight = 20;
                    doc.addImage(signatureDataUrl, 'PNG', 50, finalY - (signatureImgHeight/2) + 2 , signatureImgWidth, signatureImgHeight);
                } else {
                    console.warn("Signature data is too short (length: " + base64ImageData.length + "), possibly empty or corrupted. Drawing a line instead.");
                    doc.line(50, finalY, 120, finalY);
                }
            } else {
                console.error("Signature data URL is not a valid PNG image. Drawing a line instead.");
                doc.line(50, finalY, 120, finalY);
            }
        } catch (e) {
            console.error("Error adding signature image to PDF:", e);
            doc.line(50, finalY, 120, finalY);
        }
    } else {
        doc.line(50, finalY, 120, finalY);
    }
    
    if (action === 'save') { doc.save(`PO_${id}.pdf`); } 
    else if (action === 'preview') { doc.output('dataurlnewwindow'); }
    else if (action === 'datauristring') { return doc.output('datauristring'); }
}

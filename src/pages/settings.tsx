import React, { useState, useEffect } from 'react';

interface SettingsProps {
    appSettings: any;
}

const Settings: React.FC<SettingsProps> = ({ appSettings }) => {
    const [settings, setSettings] = useState(appSettings);

    useEffect(() => {
        setSettings(appSettings);
    }, [appSettings]);

    const handleGeneralChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
        const { name, value } = e.target;
        setSettings((prev: any) => ({ ...prev, [name]: value }));
    };

    const handleEmailChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const { name, value } = e.target;
        setSettings((prev: any) => ({ ...prev, [name]: value }));
    };

    const handleGeneralSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        try {
            const { company_name, default_shipping_zip_code, default_email_body } = settings;
            const body = JSON.stringify({ 
                company_name, 
                default_shipping_zip_code, 
                default_email_body: default_email_body.replace(/\[vendorCompany\]/g, '[vendorCompanyName]')
            });
            const response = await fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body,
            });
            if (!response.ok) throw new Error('Failed to save general settings');
            alert('General settings saved successfully!');
        } catch (error) {
            console.error(error);
            alert('Error saving general settings.');
        }
    };

    const handleEmailSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        try {
            const { email_address, app_password, email_cc, email_bcc, GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN } = settings;
            const response = await fetch('/api/settings/email', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email_address, app_password, email_cc, email_bcc, GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN }),
            });
            if (!response.ok) throw new Error('Failed to save email settings');
            alert('Email settings saved successfully!');
        } catch (error) {
            console.error(error);
            alert('Error saving email settings.');
        }
    };

    return (
        <div className="max-w-7xl mx-auto">
            <h1 className="text-3xl font-bold text-slate-800">Settings</h1>
            <div className="mt-6 space-y-8">
                <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200">
                    <h2 className="text-xl font-semibold text-slate-700 border-b border-slate-200 pb-3 mb-4">General Settings</h2>
                    <form onSubmit={handleGeneralSubmit} className="space-y-4">
                        <div>
                            <label htmlFor="companyName" className="block text-sm font-medium text-slate-600">Company Name</label>
                            <input type="text" id="companyName" name="company_name" value={settings.company_name} onChange={handleGeneralChange} className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" />
                        </div>
                        <div>
                            <label htmlFor="shippingZipCode" className="block text-sm font-medium text-slate-600">Default Shipping Zip Code</label>
                            <input type="text" id="shippingZipCode" name="default_shipping_zip_code" value={settings.default_shipping_zip_code} onChange={handleGeneralChange} className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" />
                        </div>
                        <div>
                            <label htmlFor="defaultEmailBody" className="block text-sm font-medium text-slate-600">Default Email Body</label>
                            <textarea id="defaultEmailBody" name="default_email_body" value={settings.default_email_body} onChange={handleGeneralChange} rows={5} className="mt-1 block w-full text-sm px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500"></textarea>
                            <p className="mt-2 text-xs text-slate-500">
                                Available placeholders: [customerName], [vendorCompanyName], [orderID], [yourCompany]
                            </p>
                        </div>
                        <div className="pt-2">
                            <button type="submit" className="px-4 py-2 bg-orange-600 text-white font-semibold rounded-md hover:bg-orange-700 transition-colors shadow">Save General Settings</button>
                        </div>
                    </form>
                </div>

                <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200">
                    <h2 className="text-xl font-semibold text-slate-700 border-b border-slate-200 pb-3 mb-4">Email Server Settings</h2>
                    <form onSubmit={handleEmailSubmit} className="space-y-4">
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <label htmlFor="emailAddress" className="block text-sm font-medium text-slate-600">Email Address</label>
                                <input type="email" id="emailAddress" name="email_address" value={settings.email_address} onChange={handleEmailChange} className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" />
                            </div>
                            <div>
                                <label htmlFor="appPassword" className="block text-sm font-medium text-slate-600">App Password</label>
                                <input type="password" id="appPassword" name="app_password" value={settings.app_password} onChange={handleEmailChange} className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" />
                            </div>
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <label htmlFor="emailCc" className="block text-sm font-medium text-slate-600">CC</label>
                                <input type="text" id="emailCc" name="email_cc" value={settings.email_cc} onChange={handleEmailChange} className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" />
                                <small className="text-xs text-slate-500">Comma-separated list of emails to CC.</small>
                            </div>
                            <div>
                                <label htmlFor="emailBcc" className="block text-sm font-medium text-slate-600">BCC</label>
                                <input type="text" id="emailBcc" name="email_bcc" value={settings.email_bcc} onChange={handleEmailChange} className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" />
                                <small className="text-xs text-slate-500">Comma-separated list of emails to BCC.</small>
                            </div>
                        </div>
                        <div>
                            <label htmlFor="gmailClientId" className="block text-sm font-medium text-slate-600">Gmail Client ID</label>
                            <input type="text" id="gmailClientId" name="GMAIL_CLIENT_ID" value={settings.GMAIL_CLIENT_ID} onChange={handleEmailChange} className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" />
                        </div>
                        <div>
                            <label htmlFor="gmailClientSecret" className="block text-sm font-medium text-slate-600">Gmail Client Secret</label>
                            <input type="password" id="gmailClientSecret" name="GMAIL_CLIENT_SECRET" value={settings.GMAIL_CLIENT_SECRET} onChange={handleEmailChange} className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" />
                        </div>
                        <div>
                            <label htmlFor="gmailRefreshToken" className="block text-sm font-medium text-slate-600">Gmail Refresh Token</label>
                            <input type="password" id="gmailRefreshToken" name="GMAIL_REFRESH_TOKEN" value={settings.GMAIL_REFRESH_TOKEN} onChange={handleEmailChange} className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" />
                        </div>
                        <div className="pt-2">
                            <button type="submit" className="px-4 py-2 bg-orange-600 text-white font-semibold rounded-md hover:bg-orange-700 transition-colors shadow">Save Email Settings</button>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    );
};

export default Settings;

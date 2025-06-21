import React, { useState, useEffect } from 'react';

interface Package {
    id_val: string;
    name: string;
    type: string;
    contents: { itemCode: string; quantity: number }[];
    contents_raw_text: string;
}

const ManagePackagesPage = () => {
    const [packages, setPackages] = useState<Record<string, Package>>({});
    const [isModalOpen, setIsModalOpen] = useState(false);
    const [modalMode, setModalMode] = useState<'add' | 'edit'>('add');
    const [currentPackage, setCurrentPackage] = useState<Package | null>(null);

    useEffect(() => {
        loadPackages();
    }, []);

    const loadPackages = async () => {
        try {
            const response = await fetch('/api/packages');
            const data = await response.json();
            setPackages(data);
        } catch (error) {
            console.error('Error loading packages:', error);
        }
    };

    const openModal = (mode: 'add' | 'edit' = 'add', pkg: Package | null = null) => {
        setModalMode(mode);
        if (mode === 'edit' && pkg) {
            const contentsText = pkg.contents.map((c) => `${c.itemCode}:${c.quantity}`).join('\n');
            setCurrentPackage({ ...pkg, contents_raw_text: contentsText });
        } else {
            setCurrentPackage({ name: '', id_val: '', type: 'package', contents: [], contents_raw_text: '' });
        }
        setIsModalOpen(true);
    };

    const closeModal = () => {
        setIsModalOpen(false);
        setCurrentPackage(null);
    };

    const handleInputChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
        const { id, value } = e.target;
        if (currentPackage) {
            setCurrentPackage({ ...currentPackage, [id]: value });
        }
    };
    
    const handleFormSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!currentPackage || !currentPackage.name || !currentPackage.id_val) {
            alert('Package Name and Package ID are required.');
            return;
        }

        const packageData = {
            name: currentPackage.name.trim(),
            id_val: parseInt(currentPackage.id_val, 10),
            type: currentPackage.type.trim(),
            contents_raw_text: currentPackage.contents_raw_text.trim()
        };

        let url = '/api/packages';
        let method = 'POST';

        if (modalMode === 'edit') {
            url = `/api/packages/${currentPackage.id_val}`;
            method = 'PUT';
        }

        try {
            const response = await fetch(url, {
                method: method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(packageData),
            });
            const result = await response.json();
            if (response.ok) {
                closeModal();
                loadPackages();
                alert(result.message || 'Package saved successfully!');
            } else {
                alert('Error saving package: ' + (result.message || 'Unknown error'));
            }
        } catch (error) {
            console.error('Error saving package:', error);
            alert('Error saving package. See console for details.');
        }
    };

    const deletePackage = async (packageId: string) => {
        if (!confirm('Are you sure you want to delete this package? This action cannot be undone.')) {
            return;
        }

        try {
            const response = await fetch(`/api/packages/${packageId}`, { method: 'DELETE' });
            const result = await response.json();
            if (response.ok) {
                loadPackages();
                alert(result.message || 'Package deleted successfully!');
            } else {
                alert('Error deleting package: ' + (result.message || 'Unknown error'));
            }
        } catch (error) {
            console.error('Error deleting package:', error);
            alert('Error deleting package. See console for details.');
        }
    };

    return (
        <>
            <div className="max-w-7xl mx-auto">
                <h1 className="text-3xl font-bold text-slate-800">Manage Packages</h1>

                <div className="mt-6 bg-white p-6 rounded-lg shadow-sm border border-slate-200">
                    <button type="button" className="px-4 py-2 bg-orange-600 text-white font-semibold rounded-md hover:bg-orange-700 transition-colors shadow" onClick={() => openModal('add')}>
                        + Add New Package
                    </button>
                    <div className="mt-6 overflow-x-auto">
                        <table className="w-full text-sm text-left text-slate-500">
                            <thead className="text-xs text-slate-700 uppercase bg-slate-100">
                                <tr>
                                    <th className="px-4 py-3">Name</th>
                                    <th className="px-4 py-3">ID</th>
                                    <th className="px-4 py-3">Type</th>
                                    <th className="px-4 py-3">Contents</th>
                                    <th className="px-4 py-3 text-center">Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                {Object.values(packages).map((pkg) => (
                                    <tr key={pkg.id_val} className="bg-white border-b hover:bg-slate-50">
                                        <td className="px-4 py-3 font-medium text-slate-800">{pkg.name || 'N/A'}</td>
                                        <td className="px-4 py-3">{pkg.id_val || 'N/A'}</td>
                                        <td className="px-4 py-3">{pkg.type || 'N/A'}</td>
                                        <td className="px-4 py-3">{pkg.contents.map((c) => `${c.itemCode} (x${c.quantity})`).join(', ') || 'N/A'}</td>
                                        <td className="px-4 py-3 text-center">
                                            <div className="flex items-center justify-center space-x-2">
                                                <button className="p-1 text-slate-500 hover:text-orange-600" onClick={() => openModal('edit', pkg)}>
                                                    <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path d="M17.414 2.586a2 2 0 00-2.828 0L7 10.172V13h2.828l7.586-7.586a2 2 0 000-2.828z" /><path fillRule="evenodd" d="M2 6a2 2 0 012-2h4a1 1 0 010 2H4v10h10v-4a1 1 0 112 0v4a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" clipRule="evenodd" /></svg>
                                                </button>
                                                <button className="p-1 text-slate-500 hover:text-red-600" onClick={() => deletePackage(pkg.id_val)}>
                                                    <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fillRule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm4 0a1 1 0 012 0v6a1 1 0 11-2 0V8z" clipRule="evenodd" /></svg>
                                                </button>
                                            </div>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            {isModalOpen && (
                <div className="fixed inset-0 bg-black bg-opacity-50 z-50 flex justify-center items-center p-4">
                    <div className="bg-white rounded-lg shadow-xl p-6 sm:p-8 w-full max-w-xl max-h-[90vh] overflow-y-auto" role="document">
                        <div className="flex justify-between items-center pb-4 border-b border-slate-200">
                            <h5 className="text-xl font-bold text-slate-800">{modalMode === 'add' ? 'Add Package' : 'Edit Package'}</h5>
                            <button type="button" className="p-1 text-slate-400 hover:text-slate-600" onClick={closeModal} aria-label="Close">
                                <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" /></svg>
                            </button>
                        </div>
                        <form onSubmit={handleFormSubmit} className="mt-6 space-y-4">
                            <div>
                                <label htmlFor="name" className="block text-sm font-medium text-slate-600">Package Name</label>
                                <input type="text" id="name" value={currentPackage?.name || ''} onChange={handleInputChange} className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" required />
                            </div>
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                                <div>
                                    <label htmlFor="id_val" className="block text-sm font-medium text-slate-600">Package ID (e.g., 4000)</label>
                                    <input type="number" id="id_val" value={currentPackage?.id_val || ''} onChange={handleInputChange} className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" required />
                                </div>
                                <div>
                                    <label htmlFor="type" className="block text-sm font-medium text-slate-600">Type</label>
                                    <input type="text" id="type" value={currentPackage?.type || ''} onChange={handleInputChange} className="mt-1 block w-full px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm" />
                                </div>
                            </div>
                            <div>
                                <label htmlFor="contents_raw_text" className="block text-sm font-medium text-slate-600">Contents (one itemCode:quantity per line)</label>
                                <textarea id="contents_raw_text" value={currentPackage?.contents_raw_text || ''} onChange={handleInputChange} rows={5} className="mt-1 block w-full text-sm px-3 py-2 bg-white border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-orange-500 focus:border-orange-500" placeholder="Example:&#10;8000:2&#10;1000:1"></textarea>
                            </div>
                            <div className="mt-6 pt-4 border-t border-slate-200 flex justify-end space-x-3">
                                <button type="button" className="px-4 py-2 bg-slate-200 text-slate-700 font-semibold rounded-md hover:bg-slate-300 transition-colors" onClick={closeModal}>Close</button>
                                <button type="submit" className="px-4 py-2 bg-orange-600 text-white font-semibold rounded-md hover:bg-orange-700 transition-colors">Save Package</button>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </>
    );
};

export default ManagePackagesPage;

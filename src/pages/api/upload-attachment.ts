import type { NextApiRequest, NextApiResponse } from 'next';
import formidable from 'formidable';
import fs from 'fs';
import path from 'path';
import { randomBytes } from 'crypto';

const UPLOAD_FOLDER = path.resolve(process.cwd(), 'uploads');

if (!fs.existsSync(UPLOAD_FOLDER)) {
    fs.mkdirSync(UPLOAD_FOLDER, { recursive: true });
}

export const config = {
    api: {
        bodyParser: false,
    },
};

export default async function handler(
    req: NextApiRequest,
    res: NextApiResponse
) {
    if (req.method !== 'POST') {
        res.setHeader('Allow', ['POST']);
        return res.status(405).end(`Method ${req.method} Not Allowed`);
    }

    const form = formidable({
        uploadDir: UPLOAD_FOLDER,
        keepExtensions: true,
        filename: (name, ext, part, form) => {
            const originalFilename = part.originalFilename || 'file';
            const uniqueId = randomBytes(4).toString('hex');
            const filename = path.parse(originalFilename).name;
            return `${filename}_${uniqueId}${ext}`;
        }
    });

    form.parse(req, (err, fields, files) => {
        if (err) {
            console.error('Error parsing form:', err);
            return res.status(500).json({ status: 'error', message: 'Error parsing form' });
        }

        const file = files.file;

        if (!file) {
            return res.status(400).json({ status: 'error', message: 'No file uploaded' });
        }

        const uploadedFile = Array.isArray(file) ? file[0] : file;

        res.status(200).json({
            status: 'success',
            message: 'File uploaded successfully',
            originalFilename: uploadedFile.originalFilename,
            uniqueFilename: uploadedFile.newFilename,
        });
    });
}
